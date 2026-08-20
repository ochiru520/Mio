<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { AlertCircle, ArrowLeft, ArrowRight, Bot, Check, CheckCircle2, Cpu, Database, RefreshCw, ShieldCheck, Sparkles } from '@lucide/vue'
import {
  createOnboardingProvider,
  discoverOnboardingModels,
  testOnboardingModel,
} from '../services/onboardingApi.js'
import { canContinueOnboardingStep } from '../onboardingGates.js'
import { apiRequest } from '../services/api.js'
import DependencyCenter from './DependencyCenter.vue'

const props = defineProps({
  environment: { type: Object, default: null },
  onboarding: { type: Object, default: null },
  environmentBusy: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
  error: { type: String, default: '' },
})
const emit = defineEmits(['complete', 'refresh-environment'])

const step = ref(0)
const form = reactive({
  assistant_name: 'Mio',
  user_address: '你',
  web_search_enabled: false,
  proactive_enabled: false,
  daily_diary_auto_enabled: false,
  qq_enabled: false,
})
const provider = reactive({
  preset_id: 'openai',
  provider_kind: 'official',
  provider_protocol: 'openai',
  default_api_mode: 'auto',
  provider_name: 'OpenAI 官方',
  base_url: 'https://api.openai.com/v1',
  api_key: '',
  manual_model: '',
})
const providerBusy = ref('')
const providerError = ref('')
const providerMessage = ref('')
const providerMeta = ref(null)
const discoveredModels = ref([])
const modelSetupComplete = ref(false)
const providerPresets = ref([])

const steps = [
  { title: '检查这台电脑', subtitle: '先确认核心环境，再了解哪些扩展能力已经就绪', icon: Cpu },
  { title: '欢迎使用 Mio', subtitle: '先确认数据保存位置和云端边界', icon: Sparkles },
  { title: '彼此怎么称呼', subtitle: '这些名字以后都可以在设置中修改', icon: Bot },
  { title: '模型服务', subtitle: '对话需要至少一个可用的模型供应商', icon: Database },
  { title: '敏感能力', subtitle: '新安装默认关闭，由你逐项决定', icon: ShieldCheck },
  { title: '准备完成', subtitle: '设置会保存在本机数据目录', icon: Check },
]

const current = computed(() => steps[step.value])
const configuredModels = computed(() => props.environment?.optional?.find((item) => item.id === 'cloud_model'))
const hasStoredModel = computed(() => configuredModels.value?.status === 'configured')
const hasVerifiedModel = computed(() => props.onboarding?.model_verification?.verified === true || modelSetupComplete.value)
const selectedProviderModels = computed(() => discoveredModels.value.filter((item) => item.selected))
const canContinue = computed(() => canContinueOnboardingStep({
  step: step.value,
  coreReady: props.environment?.core_ready,
  environmentBusy: props.environmentBusy,
  assistantName: form.assistant_name,
  userAddress: form.user_address,
  modelVerified: hasVerifiedModel.value,
  providerBusy: Boolean(providerBusy.value),
}))

function environmentStatusLabel(status) {
  return {
    available: '可以使用',
    configured: '已经配置',
    unconfigured: '尚未配置',
    missing: '需要处理',
    unsupported: '环境不满足',
  }[status] || '尚未检查'
}

function handleDependencyNavigate(target) {
  if (target === 'settings-models') {
    step.value = 3
    return
  }
  // 向导里没有语音配置步骤；进入应用后到设置里配置
  providerError.value = '云端语音稍后配置：完成向导后，到「设置 > 桌宠 > 语音」选择云端语音并填写 API Key。'
  step.value = 4
}

function environmentStatusIcon(status) {
  return ['available', 'configured'].includes(status) ? CheckCircle2 : AlertCircle
}

function next() {
  if (!canContinue.value || props.busy) return
  if (step.value < steps.length - 1) step.value += 1
  else emit('complete', { ...form })
}

function selectProviderPreset(presetId) {
  if (presetId === 'compatible') {
    Object.assign(provider, { preset_id: '', provider_kind: 'relay', provider_protocol: 'openai', default_api_mode: 'auto', provider_name: '', base_url: '' })
  } else {
    const preset = providerPresets.value.find((item) => item.id === presetId)
    if (!preset) return
    Object.assign(provider, {
      preset_id: preset.id,
      provider_kind: preset.kind || 'official',
      provider_protocol: preset.protocol || 'openai',
      default_api_mode: preset.default_api_mode || 'auto',
      provider_name: preset.name || '',
      base_url: preset.base_url || '',
    })
  }
  discoveredModels.value = []
  providerMeta.value = null
  providerError.value = ''
  providerMessage.value = ''
}

onMounted(async () => {
  try {
    const result = await apiRequest('/api/agent/models/provider-presets')
    providerPresets.value = result.presets || []
  } catch {
    providerPresets.value = []
  }
})

async function discoverModels() {
  if (providerBusy.value) return
  providerBusy.value = 'discover'
  providerError.value = ''
  providerMessage.value = ''
  try {
    const result = await discoverOnboardingModels({
      provider_kind: provider.provider_kind,
      provider_protocol: provider.provider_protocol,
      default_api_mode: provider.default_api_mode,
      preset_id: provider.preset_id,
      base_url: provider.base_url,
      api_key: provider.api_key,
    })
    let selectedFirstSupported = false
    discoveredModels.value = (result.models || []).map((item) => {
      const selected = item.api_supported !== false && !selectedFirstSupported
      if (selected) selectedFirstSupported = true
      return { ...item, selected }
    })
    providerMeta.value = result
    if (!discoveredModels.value.length) throw new Error('供应商没有返回可用模型，可以在下方手动填写模型 ID。')
    providerMessage.value = `已发现 ${discoveredModels.value.length} 个模型，请选择要保存的版本。`
  } catch (error) {
    discoveredModels.value = []
    providerMeta.value = null
    providerError.value = `${error.message} 仍可手动填写模型 ID。`
  } finally {
    providerBusy.value = ''
  }
}

function requestedProviderModels() {
  if (selectedProviderModels.value.length) return selectedProviderModels.value
  const manual = provider.manual_model.trim()
  if (!manual) return []
  return [{ model: manual, display_name: manual, family_name: manual }]
}

async function saveAndTestProvider() {
  if (providerBusy.value) return
  const requestedModels = requestedProviderModels()
  if (!provider.provider_name.trim()) {
    providerError.value = '请填写供应商名称。'
    return
  }
  if (!provider.base_url.trim()) {
    providerError.value = '请填写 API 地址。'
    return
  }
  if (!provider.api_key.trim()) {
    providerError.value = '请填写 API Key。'
    return
  }
  if (!requestedModels.length) {
    providerError.value = '请先发现并选择模型，或手动填写模型 ID。'
    return
  }
  providerBusy.value = 'save'
  providerError.value = ''
  providerMessage.value = '正在保存供应商并执行最小聊天测试。'
  try {
    const created = await createOnboardingProvider({
      provider_name: provider.provider_name,
      provider_kind: provider.provider_kind,
      provider_protocol: provider.provider_protocol,
      default_api_mode: provider.default_api_mode,
      preset_id: provider.preset_id,
      auth_scheme: providerMeta.value?.auth_scheme || 'auto',
      base_url: providerMeta.value?.resolved_api_base_url || provider.base_url,
      api_key: provider.api_key,
      models: requestedModels.map((item) => ({
        model: item.model,
        display_name: item.display_name || item.model,
        family_name: item.family_name || item.model,
        variant_name: item.variant_name || '',
        supports_vision: Boolean(item.supports_vision),
        supports_tool_calls: item.supports_tool_calls ?? null,
        supports_structured_output: item.supports_structured_output ?? true,
        context_window_tokens: Number(item.context_window_tokens || 32768),
        cached_input_price_cny_per_million: Number(item.cached_input_price_cny_per_million || 0),
        input_price_cny_per_million: Number(item.input_price_cny_per_million || 0),
        output_price_cny_per_million: Number(item.output_price_cny_per_million || 0),
        pricing_source: item.pricing_source || '',
        api_mode: item.api_mode || '',
      })),
    })
    const model = created.models?.[0]
    if (!model?.id) throw new Error('供应商已保存，但没有生成可测试的模型。')
    const tested = await testOnboardingModel(model.id)
    modelSetupComplete.value = true
    provider.api_key = ''
    providerMessage.value = tested.message || `模型 ${model.display_name || model.model} 已通过聊天测试。`
    emit('refresh-environment')
  } catch (error) {
    providerError.value = error.message
    providerMessage.value = ''
  } finally {
    providerBusy.value = ''
  }
}
</script>

<template>
  <main class="onboarding-shell">
    <section class="onboarding-card">
      <header>
        <div class="onboarding-mark"><component :is="current.icon" :size="24" /></div>
        <div><h1>{{ current.title }}</h1><p>{{ current.subtitle }}</p></div>
        <span>{{ step + 1 }} / {{ steps.length }}</span>
      </header>

      <div class="onboarding-progress"><i v-for="(_, index) in steps" :key="index" :class="{ active: index <= step }" /></div>

      <section v-if="step === 0" class="onboarding-content environment-check">
        <div v-if="environmentBusy && !environment" class="environment-loading"><RefreshCw class="spin" :size="22" /><span>正在检查本机环境</span></div>
        <template v-else-if="environment">
          <div class="environment-summary">
            <span><strong>{{ environment.system?.memory_label || '未知' }}</strong><small>内存</small></span>
            <span><strong>{{ environment.system?.free_disk_label || '未知' }}</strong><small>数据盘可用</small></span>
            <span><strong>{{ environment.system?.gpus?.[0]?.name || '未识别' }}</strong><small>主要显卡</small></span>
          </div>
          <div class="environment-heading">
            <div><strong>应用必需环境</strong><small>{{ environment.summary.required_ready }} / {{ environment.summary.required_total }} 项通过</small></div>
            <button type="button" :disabled="environmentBusy" @click="emit('refresh-environment')"><RefreshCw :class="{ spin: environmentBusy }" :size="14" />重新检查</button>
          </div>
          <div class="environment-list required">
            <article v-for="item in environment.required" :key="item.id" :class="item.status">
              <component :is="environmentStatusIcon(item.status)" :size="17" />
              <div><strong>{{ item.label }}</strong><p>{{ item.detail }}</p><small v-if="item.action">{{ item.action }}</small></div>
              <b>{{ environmentStatusLabel(item.status) }}</b>
            </article>
          </div>
          <DependencyCenter @refresh-environment="emit('refresh-environment')" @navigate="handleDependencyNavigate" />
        </template>
        <div v-else class="environment-loading failed"><AlertCircle :size="22" /><span>环境检查暂时不可用，请重新检查</span><button type="button" @click="emit('refresh-environment')">重新检查</button></div>
      </section>

      <section v-else-if="step === 1" class="onboarding-content privacy-intro">
        <article><Database :size="19" /><div><strong>本地保存</strong><p>聊天、日记、记忆、角色设置和完整备份都保存在当前应用数据目录；便携版的数据就在程序旁边</p></div></article>
        <article><Bot :size="19" /><div><strong>云端模型</strong><p>只有发起对话、联网搜索或云端视觉时，对应内容才会发送给你配置的供应商</p></div></article>
        <article><ShieldCheck :size="19" /><div><strong>随时暂停</strong><p>设置里可以一键暂停联网、QQ、主动联系、自动记录、屏幕与声音观察</p></div></article>
      </section>

      <section v-else-if="step === 2" class="onboarding-content onboarding-fields">
        <label><span>角色名字</span><input v-model.trim="form.assistant_name" maxlength="80" autocomplete="off" /></label>
        <label><span>她怎么称呼你</span><input v-model.trim="form.user_address" maxlength="80" autocomplete="off" /></label>
      </section>

      <section v-else-if="step === 3" class="onboarding-content model-check">
        <div :class="['onboarding-status', { ready: hasVerifiedModel }]">
          <component :is="hasVerifiedModel ? Check : Bot" :size="20" />
          <div><strong>{{ hasVerifiedModel ? (providerMessage || '模型已经通过真实聊天测试') : (hasStoredModel ? '已保存模型，但还需要通过聊天测试' : '配置一个模型后即可开始对话；也可以先跳过，稍后在设置里配置') }}</strong><p>{{ hasVerifiedModel ? '可以继续进入应用，之后仍可在设置中添加或删除供应商。' : '这一步会读取模型列表，并发送一条最小聊天请求确认 Key 和模型真正可用。想先用着也可以直接跳过。' }}</p></div>
        </div>
        <div v-if="!hasVerifiedModel" class="onboarding-provider-form">
          <div class="onboarding-provider-presets">
            <select :value="provider.preset_id" @change="selectProviderPreset($event.target.value)"><option v-for="preset in providerPresets" :key="preset.id" :value="preset.id">{{ preset.name }}</option></select>
            <button type="button" :class="{ active: provider.provider_kind === 'relay' }" @click="selectProviderPreset('compatible')">兼容网关</button>
          </div>
          <div class="onboarding-provider-fields">
            <label><span>供应商名称</span><input v-model.trim="provider.provider_name" autocomplete="off" placeholder="例如：我的模型服务" /></label>
            <label><span>API 地址</span><input v-model.trim="provider.base_url" autocomplete="url" placeholder="https://example.com/v1" /></label>
            <label><span>接口模式</span><select v-model="provider.default_api_mode"><option value="auto">自动识别</option><option value="responses">Responses API（Codex）</option><option value="chat_completions">Chat Completions</option></select></label>
            <label class="wide"><span>API Key</span><input v-model="provider.api_key" type="password" autocomplete="new-password" placeholder="只加密保存在当前 Windows 用户下" /></label>
          </div>
          <div class="onboarding-provider-actions"><button type="button" :disabled="Boolean(providerBusy) || !provider.api_key.trim()" @click="discoverModels"><RefreshCw :class="{ spin: providerBusy === 'discover' }" :size="14" />{{ providerBusy === 'discover' ? '正在读取' : '读取模型' }}</button></div>
          <div v-if="discoveredModels.length" class="onboarding-model-list">
            <label v-for="item in discoveredModels" :key="item.model"><input v-model="item.selected" type="checkbox" :disabled="item.api_supported === false" /><span><strong>{{ item.display_name || item.model }}</strong><small>{{ item.model }}{{ item.api_supported === false ? ' · /messages 暂不支持' : '' }}</small></span></label>
          </div>
          <label class="onboarding-manual-model"><span>没有模型列表时手动填写模型 ID</span><input v-model.trim="provider.manual_model" placeholder="例如：gpt-5-mini" /></label>
          <button class="onboarding-provider-save" type="button" :disabled="Boolean(providerBusy)" @click="saveAndTestProvider"><Check :size="14" />{{ providerBusy === 'save' ? '正在保存并测试' : '保存并测试' }}</button>
          <p v-if="providerError" class="onboarding-inline-error">{{ providerError }}</p>
          <p v-else-if="providerMessage" class="onboarding-inline-success">{{ providerMessage }}</p>
        </div>
        <p class="onboarding-note">配置并测试模型后即可开始对话；也可以先跳过，之后到「模型与 API」设置里随时配置。API Key 使用 Windows DPAPI 加密保存，不会回显，不进入备份、日志或 Git</p>
      </section>

      <section v-else-if="step === 4" class="onboarding-content capability-list">
        <label><span><strong>联网搜索</strong><small>不知道或信息可能过期时查询网络</small></span><input v-model="form.web_search_enabled" type="checkbox" /></label>
        <label><span><strong>主动联系</strong><small>应用开着且长时间没有对话时主动发消息</small></span><input v-model="form.proactive_enabled" type="checkbox" /></label>
        <label><span><strong>自动日记</strong><small>当天未手动生成时按设定时间自动整理</small></span><input v-model="form.daily_diary_auto_enabled" type="checkbox" /></label>
        <label><span><strong>QQ 通道</strong><small>连接 NapCat 后同步 QQ 私聊或指定群聊</small></span><input v-model="form.qq_enabled" type="checkbox" /></label>
        <p>屏幕观察、系统声音与 QQ 图片上传保持关闭，需要时再到设置中单独开启</p>
      </section>

      <section v-else class="onboarding-content finish-summary">
        <Check :size="30" />
        <h2>{{ form.assistant_name }}已经准备好了</h2>
        <p>数据目录会自动建立完整备份。以后更换电脑或重装应用，可以从“设置 > 数据与隐私”恢复</p>
      </section>

      <p v-if="error" class="onboarding-error">{{ error }}</p>
      <footer>
        <button type="button" :disabled="step === 0 || busy" @click="step -= 1"><ArrowLeft :size="16" />上一步</button>
        <button class="primary" type="button" :disabled="!canContinue || busy" @click="next">
          {{ step === steps.length - 1 ? (busy ? '正在保存' : '进入应用') : '继续' }}
          <ArrowRight v-if="step < steps.length - 1" :size="16" /><Check v-else :size="16" />
        </button>
      </footer>
    </section>
  </main>
</template>
