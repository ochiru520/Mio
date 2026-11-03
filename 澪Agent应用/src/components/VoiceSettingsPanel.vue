<script setup>
import SelectField from './SelectField.vue'
import { computed, ref, watch } from 'vue'
import { Check, Download, Eraser, PhoneCall, Play, Power, Trash2, Upload, Volume2 } from '../icons/index.js'
import { createVoiceProfile, removeVoiceProfile } from '../voiceProfiles.js'
import { getVoiceModelGuide } from '../voiceModelCatalog.js'
import { voiceRuntimeIdentity } from '../voiceRuntimeIdentity.js'
import RealtimeVoiceModal from './RealtimeVoiceModal.vue'

const props = defineProps({
  status: { type: Object, required: true },
  modelOptions: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
  rangeInputStyle: { type: Function, required: true },
})

const emit = defineEmits(['control-runtime', 'test', 'upload-reference', 'upload-weight', 'export-package', 'import-package'])
const voicePackageInput = ref(null)

function startVoicePackageImport() {
  if (window.pywebview?.api?.import_voice_package) {
    emit('import-package')
    return
  }
  voicePackageInput.value?.click()
}

const CLOUD_SPEAKERS = [
  { id: 'zh_female_vv_uranus_bigtts', name: '薇薇（活泼女声）' },
  { id: 'zh_female_xiaohe_uranus_bigtts', name: '小荷（清甜女声）' },
  { id: 'zh_male_yunzhou_uranus_bigtts', name: '云舟（沉稳男声）' },
  { id: 'zh_male_xiaotian_uranus_bigtts', name: '小天（清亮男声）' },
]

const selectedProfileId = ref('')
const newProfileName = ref('')
const voiceCreateDetails = ref(null)
const cloudApiKeyDraft = computed({
  get: () => settings.value.cloud_tts_api_key === '__clear__' ? '' : (settings.value.cloud_tts_api_key || ''),
  set: value => { settings.value.cloud_tts_api_key = value.trim() },
})
const realtimeVisible = ref(false)
const testText = ref('你好，我是澪。我会一边思考，一边把想说的话告诉你。')
const settings = computed(() => props.status?.pet?.settings || {})
const translationModelUnavailable = computed(() => Boolean(
  settings.value.speech_translation_model_id
  && !props.modelOptions.some(model => model.id === settings.value.speech_translation_model_id),
))
const runtime = computed(() => props.status?.voice_runtime || {})
const actualVoice = computed(() => voiceRuntimeIdentity(runtime.value, settings.value))
const runtimeEngineMatches = computed(() => runtime.value.engine === engine.value
  && (engine.value !== 'gpt_sovits' || runtime.value.local_voice_runtime === settings.value.local_voice_runtime))
const cloudState = computed(() => runtime.value.cloud_tts || {})
const modelGuide = computed(() => getVoiceModelGuide(engine.value, settings.value.local_voice_runtime, settings.value.indextts2_version))
const engine = computed(() => (['cloud', 'indextts2', 'gpt_sovits_v5', 'aniflive'].includes(settings.value.voice_engine) ? settings.value.voice_engine : 'gpt_sovits'))
const runtimeMatchesEngine = computed(() => runtime.value.engine === engine.value
  && (engine.value !== 'gpt_sovits' || (runtime.value.local_voice_runtime === settings.value.local_voice_runtime
    && (settings.value.local_voice_runtime !== 'mio_v2' || nativeProfileMatchesRuntime.value))))
const nativeProfileMatchesRuntime = computed(() => {
  const canonical = value => String(value || '').replaceAll('\\', '/').toLowerCase()
  const weightsMatch = ['gpt', 'sovits'].every(kind => {
    const requested = selectedProfile.value?.[`gpt_sovits_${kind}_weights`]
    return Boolean(requested) && canonical(requested) === canonical(runtime.value.active_weights?.[kind])
  })
  const savedProfile = runtime.value.profiles?.[runtime.value.default_profile_id]
  return (!runtime.value.active_profile_id || runtime.value.active_profile_id === selectedProfileId.value)
    && weightsMatch
    && canonical(selectedProfile.value?.gpt_sovits_ref_audio) === canonical(runtime.value.active_reference_audio || savedProfile?.gpt_sovits_ref_audio)
    && selectedProfile.value?.gpt_sovits_prompt_text === (runtime.value.verified_reference_text ?? savedProfile?.gpt_sovits_prompt_text)
    && selectedProfile.value?.gpt_sovits_prompt_language === (runtime.value.active_reference_language || savedProfile?.gpt_sovits_prompt_language)
})
const lastSuccessfulVoiceId = computed(() => runtime.value.last_successful_profile_id || '')
const canRestoreVoice = computed(() => Boolean(settings.value.voice_profiles?.[lastSuccessfulVoiceId.value]
  && lastSuccessfulVoiceId.value !== settings.value.default_voice_profile_id))
const currentServiceRunning = computed(() => runtimeMatchesEngine.value && runtime.value.service_running)
const serviceVersion = computed(() => runtimeMatchesEngine.value ? (runtime.value.model_version || runtime.value.version || '') : '')
const profileEntries = computed(() => Object.entries(settings.value.voice_profiles || {}))
const selectedProfile = computed(() => (
  settings.value.voice_profiles?.[selectedProfileId.value] || profileEntries.value[0]?.[1] || null
))
const defaultProfile = computed(() => settings.value.voice_profiles?.[settings.value.default_voice_profile_id] || null)
const selectedIsDefault = computed(() => selectedProfileId.value === settings.value.default_voice_profile_id)
const selectedIsThirdParty = computed(() => selectedProfile.value?.engine === 'so_vits_svc')
const baseProfileEntries = computed(() => profileEntries.value.filter(([, profile]) => profile.engine !== 'so_vits_svc'))
const missingVoiceFields = computed(() => {
  if (settings.value.local_voice_runtime !== 'mio_v2' || selectedIsThirdParty.value) return []
  return [
    ['gpt_sovits_gpt_weights', 'GPT 权重'], ['gpt_sovits_sovits_weights', 'SoVITS 权重'],
    ['gpt_sovits_ref_audio', '参考音频'], ['gpt_sovits_prompt_text', '参考原文'],
  ].filter(([key]) => !String(selectedProfile.value?.[key] || '').trim()).map(([, label]) => label)
})

function profileSubtitle(profile) {
  if (profile?.engine === 'so_vits_svc') return `So-VITS-SVC（${profile.so_vits_svc_speaker || '单说话人'}）`
  if (!profile?.gpt_sovits_ref_audio) return '未上传参考音频'
  return settings.value.local_voice_runtime === 'mio_v2' ? 'V2 流式' : (settings.value.local_voice_runtime === 'genie' ? 'Genie ONNX' : '原生服务')
}

watch(
  profileEntries,
  (entries) => {
    if (entries.some(([id]) => id === selectedProfileId.value)) return
    const preferred = settings.value.default_voice_profile_id
    selectedProfileId.value = entries.some(([id]) => id === preferred) ? preferred : (entries[0]?.[0] || '')
  },
  { immediate: true },
)

function selectEngine(value) {
  settings.value.voice_engine = value
}

function testCurrentEngine() {
  if (!testText.value.trim()) return
  if (engine.value === 'cloud') {
    settings.value.cloud_tts_api_key = cloudApiKeyDraft.value.trim()
  } else if (engine.value === 'gpt_sovits') {
    makeSelectedDefault()
  }
  emit('test', engine.value === 'gpt_sovits' ? selectedProfileId.value : '', { text: testText.value.trim() })
}

function clearCloudKey() {
  cloudApiKeyDraft.value = ''
  settings.value.cloud_tts_api_key = '__clear__'
}

function openRealtime() {
  if (engine.value !== 'cloud' || !runtimeMatchesEngine.value || !cloudState.value.configured) return
  realtimeVisible.value = true
}

function deleteSelectedProfile() {
  const removed = removeVoiceProfile(
    settings.value.voice_profiles,
    {},
    settings.value.default_voice_profile_id,
    selectedProfileId.value,
  )
  if (!removed) return
  settings.value.voice_profiles = removed.profiles
  settings.value.default_voice_profile_id = removed.defaultId
  selectedProfileId.value = removed.selectedId
}

function addVoiceProfile() {
  const created = createVoiceProfile(settings.value.voice_profiles, newProfileName.value)
  if (!created) return
  created.profile.use_emotion_references = false
  settings.value.voice_profiles = { ...(settings.value.voice_profiles || {}), [created.id]: created.profile }
  selectedProfileId.value = created.id
  settings.value.local_voice_runtime = 'mio_v2'
  newProfileName.value = ''
  if (voiceCreateDetails.value) voiceCreateDetails.value.open = false
}

function startSelectedVoice() {
  makeSelectedDefault()
  emit('control-runtime', 'start')
}

function restorePreviousVoice() {
  if (!canRestoreVoice.value) return
  selectedProfileId.value = lastSuccessfulVoiceId.value
  settings.value.local_voice_runtime = 'mio_v2'
  startSelectedVoice()
}

function fileName(value) {
  return String(value || '').split(/[\\/]/).at(-1) || '尚未导入'
}

function makeSelectedDefault() {
  if (selectedProfileId.value) settings.value.default_voice_profile_id = selectedProfileId.value
}
</script>

<template>
  <div class="settings-section-block voice-settings-panel">
    <div class="settings-block-heading">
      <div>
        <h2>角色语音</h2>

      </div>
      <span v-if="engine === 'cloud'" :class="['connection-label', { online: runtimeMatchesEngine && cloudState.configured }]">
        {{ !runtimeMatchesEngine ? '待保存并验证' : (cloudState.configured ? '云端已配置' : '云端未配置') }}
      </span>
      <span v-else :class="['connection-label', { online: currentServiceRunning }]">
        {{ runtimeEngineMatches && runtime.service_loading ? '正在加载与预热' : (!runtimeMatchesEngine ? '待保存并验证' : (currentServiceRunning ? '服务在线' : '服务未就绪')) }}
      </span>
    </div>

    <div class="voice-engine-choice" role="group" aria-label="语音引擎">
      <button type="button" :disabled="busy" :class="{ active: engine === 'cloud' }" :aria-pressed="engine === 'cloud'" @click="selectEngine('cloud')">云端语音</button>
      <button type="button" :disabled="busy" :class="{ active: engine === 'gpt_sovits' }" :aria-pressed="engine === 'gpt_sovits'" @click="selectEngine('gpt_sovits')">GPT-SoVITS</button>
      <button type="button" :disabled="busy" :class="{ active: engine === 'gpt_sovits_v5' }" :aria-pressed="engine === 'gpt_sovits_v5'" @click="selectEngine('gpt_sovits_v5')">GPT-SoVITS v5</button>
      <button type="button" :disabled="busy" :class="{ active: engine === 'aniflive' }" :aria-pressed="engine === 'aniflive'" @click="selectEngine('aniflive')">AnifLive-TTS（实验）</button>
      <button type="button" :disabled="busy" :class="{ active: engine === 'indextts2' }" :aria-pressed="engine === 'indextts2'" @click="selectEngine('indextts2')">IndexTTS</button>
    </div>
    <p class="settings-note">上方选择用于下一次保存并启动或试听，实际发声以当前运行状态为准。</p>
    <section class="voice-cloud-panel voice-runtime-identity" aria-label="当前语音运行状态">
      <div class="settings-block-heading"><strong>当前运行状态</strong><span :class="['voice-runtime-state', { online: actualVoice.running }]">{{ actualVoice.status }}</span></div>
      <dl>
        <div><dt>语音引擎</dt><dd>{{ actualVoice.engine }}</dd></div>
        <div><dt>底层模型</dt><dd>{{ actualVoice.model }}</dd></div>
        <div><dt>实际音色</dt><dd>{{ actualVoice.voice }}</dd></div>
      </dl>
      <p v-if="actualVoice.source" class="settings-note">{{ actualVoice.source }}</p>
    </section>

    <section class="voice-cloud-panel voice-model-guide" aria-label="模型选择与下载">
      <label v-if="engine === 'gpt_sovits'" class="voice-profile-wide"><span>语音模型</span><SelectField v-model="settings.local_voice_runtime"><option value="mio_v2">GPT-SoVITS V2（流式，支持自训音色）</option><option value="genie">Genie ONNX（需转换音色）</option><option value="gpt_sovits">其他原生 GPT-SoVITS 服务</option></SelectField></label>
      <div class="voice-model-links"><a v-for="link in modelGuide.links" :key="link.url" :href="link.url" target="_blank" rel="noopener noreferrer">{{ link.label }}</a></div>
      <details class="voice-model-help"><summary>模型要求与安装</summary>
        <p class="settings-note">{{ modelGuide.requirements }}</p>
        <p class="settings-note">{{ modelGuide.compatibility }}</p>
        <p class="settings-note">{{ modelGuide.setup }}</p>
      </details>
    </section>

    <div class="voice-cloud-panel">
      <label class="voice-profile-wide"><span>对比试听文字</span><textarea v-model="testText" rows="2" maxlength="600" placeholder="让各引擎朗读同一段文字" /></label>
      <div class="settings-actions-row"><button type="button" :disabled="busy || !testText.trim()" @click="testCurrentEngine"><Play :size="14" />保存并试听</button></div>
      <p v-if="runtimeEngineMatches && runtime.service_loading" class="settings-note" role="status">正在加载并预热，完成后可试听。</p>
      <div v-if="canRestoreVoice && runtimeEngineMatches && !runtime.service_loading" class="settings-actions-row"><button type="button" :disabled="busy" @click="restorePreviousVoice">重新使用上次成功的音色</button></div>
      <p v-if="runtimeEngineMatches && runtime.last_error" class="settings-note">{{ runtime.last_error }}</p>
      <p v-if="runtimeEngineMatches && runtime.managed_last_error && runtime.managed_last_error !== runtime.last_error" class="settings-note">{{ runtime.managed_last_error }}</p>
      <template v-if="['gpt_sovits_v5', 'indextts2', 'aniflive'].includes(engine)">
        <div class="settings-actions-row">
          <button type="button" :disabled="busy || (runtimeMatchesEngine && runtime.service_loading)" @click="emit('control-runtime', 'start')"><Play :size="14" />保存并启动</button>
          <button type="button" :disabled="busy || !runtimeMatchesEngine || !runtime.managed_running" @click="emit('control-runtime', 'stop')"><Power :size="14" />停止</button>
          <button type="button" :disabled="busy || (runtimeMatchesEngine && runtime.service_loading)" @click="emit('control-runtime', 'restart')">重新加载</button>
        </div>
        <p class="settings-note">本地引擎首次加载可能需要数十秒。</p>
        <p v-if="runtimeMatchesEngine && !runtime.managed_available" class="settings-note">当前地址没有匹配的本机启动配置，可连接已启动的独立服务。</p>
      </template>
    </div>

    <div v-if="engine === 'indextts2'" class="voice-cloud-panel">
      <p class="settings-note">服务地址需使用 IndexTTS 适配接口，不能填官方 WebUI 地址。</p>
      <div class="settings-form-grid">
        <details class="voice-profile-wide"><summary>高级连接设置</summary><label><span>适配服务地址</span><input v-model.trim="settings.indextts2_url" type="url" placeholder="http://127.0.0.1:11996" /><small>文本与参考音频将发送到此地址；远程服务请使用受信任设备</small></label></details>
        <label><span>预期模型版本</span><SelectField v-model="settings.indextts2_version"><option value="2.5">IndexTTS 2.5</option><option value="2">IndexTTS 2</option></SelectField><small>以服务实际加载的版本为准；此选项不会替服务下载或更换权重</small></label>
        <label><span>朗读语言</span><SelectField v-model="settings.indextts2_language"><option value="zh">中文</option><option value="en">英语</option><option value="ja">日语（2.5）</option><option value="es">西班牙语（2.5）</option><option value="ar">阿拉伯语（2.5）</option></SelectField></label>
        <label class="voice-profile-wide"><span>参考音频完整路径（Mio 所在电脑）</span><input v-model.trim="settings.indextts2_ref_audio" type="text" placeholder="可上传，或填写已有音频的完整路径" /></label>
        <label class="voice-profile-wide"><span>上传参考音频</span><input type="file" accept="audio/*,.wav,.mp3,.flac,.m4a,.ogg,.aac,.wma" :disabled="busy" @change="emit('upload-reference', $event, '')" /><small>40 MB 以内；不需要填写参考原文或 GPT/SoVITS 权重</small></label>
      </div>
      <p class="settings-note">IndexTTS 按段合成；不能直接使用 GPT-SoVITS 训练权重。</p>
      <details class="settings-note">
        <summary>启动独立服务与排障</summary>
        <p>找到 Mio 安装目录的 _internal/agent_scripts/indextts2_adapter.py（源码版在 scripts 下）。在 PowerShell 中使用 IndexTTS2 的 Python 执行下面示例，并替换为你的实际路径：</p>
        <pre style="white-space: pre-wrap; overflow-wrap: anywhere">&amp; 'D:/AI/index-tts/.venv/Scripts/python.exe' 'D:/Mio/_internal/agent_scripts/indextts2_adapter.py' --repo 'D:/AI/index-tts' --model-dir 'D:/AI/index-tts/checkpoints_2.5' --version 2.5 --bf16 --low-memory</pre>
        <p>看到“已就绪”后保存并试听。连接失败时检查终端与端口；协议不符时确认没有填 WebUI 地址；HTTP 500 时检查参考音频、权重完整性和显存。Ctrl+C 停止独立服务。完整说明与脚本同目录：indextts2_adapter.md。</p>
      </details>
    </div>

    <div v-else-if="engine === 'aniflive'" class="voice-cloud-panel">
      <p class="settings-note">AnifLive 基于 GPT-SoVITS，负责加速与流式输出；下方音色包必须与服务实际加载的模型版本匹配。</p>
      <p v-if="!runtimeMatchesEngine || runtime.numerical_verified !== true" class="settings-note">当前适配尚未通过正式精度检查；原始 V2 / Genie 音色包需要专门转换，不能直接混用。</p>
      <p v-if="runtimeMatchesEngine && runtime.numerical_verified === true" class="settings-note">当前服务报告数值验证通过；实际声音与长期稳定性仍以试听和使用为准。</p>
      <details><summary>高级连接与音色设置</summary><div class="settings-form-grid">
        <label class="voice-profile-wide"><span>服务地址</span><input v-model.trim="settings.aniflive_url" type="url" placeholder="http://127.0.0.1:9882" /></label>
        <label><span>模型包 ID</span><input v-model.trim="settings.aniflive_model" type="text" maxlength="160" placeholder="留空使用服务当前模型" /></label>
        <label><span>音色 profile ID</span><input v-model.trim="settings.aniflive_voice_profile" type="text" maxlength="160" placeholder="default" /></label>
      </div></details>
    </div>

    <div v-else-if="engine === 'gpt_sovits_v5'" class="voice-cloud-panel">
      <p class="settings-note">使用所选音色的参考音频与准确原文；V2 训练权重不能直接用于 v5。</p>
      <div class="settings-form-grid">
        <details class="voice-profile-wide"><summary>高级连接设置</summary><label><span>v5 服务地址</span><input v-model.trim="settings.gpt_sovits_v5_url" type="url" placeholder="http://127.0.0.1:9885" /></label></details>
        <label v-if="baseProfileEntries.length" class="voice-profile-wide"><span>参考音色</span><SelectField v-model="settings.default_voice_profile_id"><option v-for="([id, profile]) in baseProfileEntries" :key="id" :value="id">{{ profile.name || id }}</option></SelectField></label>
        <template v-if="defaultProfile && defaultProfile.engine !== 'so_vits_svc'">
          <label><span>参考音频原文语言</span><SelectField v-model="defaultProfile.gpt_sovits_prompt_language"><option value="zh">中文</option><option value="ja">日语</option><option value="en">英语</option><option value="yue">粤语</option></SelectField></label>
          <label class="voice-profile-wide"><span>参考音频准确原文</span><textarea v-model.trim="defaultProfile.gpt_sovits_prompt_text" rows="3" maxlength="1000" placeholder="必须与参考音频逐字一致" /></label>
          <label class="voice-profile-wide"><span>参考音频（Mio 所在电脑）</span><input v-model.trim="defaultProfile.gpt_sovits_ref_audio" type="text" placeholder="填写完整路径或上传参考音频" /></label>
          <label class="voice-profile-wide"><span>上传参考音频</span><input type="file" accept="audio/*,.wav,.mp3,.flac,.m4a,.ogg,.aac,.wma" :disabled="busy" @change="emit('upload-reference', $event, settings.default_voice_profile_id)" /></label>
        </template>
      </div>
      <p v-if="!defaultProfile || defaultProfile.engine === 'so_vits_svc'" class="settings-note">请先在「GPT-SoVITS 现有音色」中选择一个基础 TTS 默认音色，或导入音色包；变声音色不能直接作为 v5 参考配置。</p>
    </div>

    <div v-else-if="engine === 'cloud'" class="voice-cloud-panel">
      <div class="cloud-tts-guide">
        <p>在<a href="https://console.volcengine.com/speech/new/setting/apikeys" target="_blank" rel="noopener">火山引擎语音控制台</a>创建 API Key 后填入。</p>
      </div>
      <div class="settings-form-grid cloud-tts-form">
        <label class="voice-cloud-key-field">
          <span>语音 API Key</span>
          <small>{{ cloudState.configured ? '已经保存了一个 Key；留空保存表示不修改' : '填写后加密保存在当前 Windows 用户下' }}</small>
          <input v-model="cloudApiKeyDraft" type="password" autocomplete="new-password" :placeholder="cloudState.configured ? '已配置（留空不改）' : '粘贴火山引擎语音 API Key'" />
        </label>
        <label>
          <span>音色</span>
          <SelectField v-model="settings.cloud_tts_speaker">
            <option v-for="item in CLOUD_SPEAKERS" :key="item.id" :value="item.id">{{ item.name }}</option>
          </SelectField>
        </label>
        <label>
          <span>语速</span>
          <input v-model.number="settings.cloud_tts_speech_rate" type="range" min="-50" max="100" step="5" :style="rangeInputStyle(settings.cloud_tts_speech_rate, -50, 100)" />
          <small>{{ settings.cloud_tts_speech_rate || 0 }}（0 为正常）</small>
        </label>
        <label>
          <span>App ID（实时对话用，可选）</span>
          <small>只在实时语音对话时用到；只朗读不需要填</small>
          <input v-model.trim="settings.cloud_tts_app_id" type="text" autocomplete="off" placeholder="火山引擎应用 App ID" />
        </label>
        <div class="settings-actions-row cloud-tts-actions">
          <button type="button" :disabled="busy" @click="testCurrentEngine"><Play :size="14" />试听</button>
          <button type="button" :disabled="!cloudState.configured" @click="openRealtime"><PhoneCall :size="14" />实时语音对话</button>
          <button v-if="cloudState.configured" type="button" :disabled="busy" @click="clearCloudKey"><Eraser :size="14" />清除已保存的 Key</button>
        </div>
      </div>
      <p class="settings-note">Key 仅发送给火山引擎，不写入备份、日志或导出文件。</p>
    </div>

    <template v-else>
      <div class="voice-selection-row">
        <label v-if="profileEntries.length" class="voice-selection-field"><span>当前音色</span><SelectField v-model="selectedProfileId"><option v-for="([id, profile]) in profileEntries" :key="id" :value="id">{{ profile.name || id }}{{ settings.default_voice_profile_id === id ? '（默认）' : '' }}</option></SelectField></label>
        <div v-if="selectedProfile" class="voice-selection-actions">
          <button type="button" :disabled="selectedIsDefault || busy" @click="makeSelectedDefault">设为默认</button>
          <button type="button" :disabled="busy" @click="testCurrentEngine">试听</button>
          <button class="danger-button" type="button" :disabled="busy || profileEntries.length <= 1" @click="deleteSelectedProfile">删除音色</button>
        </div>
      </div>
      <details ref="voiceCreateDetails" class="voice-create-profile"><summary>添加自训音色</summary>
        <div class="voice-new-profile">
          <label><span>新音色名称</span><input v-model.trim="newProfileName" type="text" maxlength="80" placeholder="例如：我的角色音色" @keydown.enter.prevent="addVoiceProfile" /></label>
          <button type="button" :disabled="busy || !newProfileName.trim() || profileEntries.length >= 20" @click="addVoiceProfile">添加自训 V2 音色</button>
        </div>
      </details>
      <details class="voice-package-tools"><summary>导入或导出音色包</summary><div class="voice-import-row">
        <button type="button" class="voice-package-import voice-package-import-primary" :disabled="busy" @click="startVoicePackageImport">
          <Upload :size="15" />导入音色包
        </button>
        <input ref="voicePackageInput" class="voice-package-file-input" type="file" accept=".zip,application/zip" :disabled="busy" @change="emit('import-package', $event)" />
        <button type="button" :disabled="busy || !selectedProfileId || selectedIsThirdParty" :title="selectedIsThirdParty ? '第三方模型不重复导出，请保留原始模型包' : ''" @click="emit('export-package', selectedProfileId)">
          <Download :size="15" />导出音色包
        </button>
        <span class="voice-import-hint">交换包不含 GPT / SoVITS 权重，接收方需另行导入。也支持 So-VITS-SVC 4.1 包。</span>
      </div></details>

      <div v-if="profileEntries.length" class="voice-profile-layout">
        <div v-if="selectedProfile" class="voice-profile-editor">
          <div v-if="settings.local_voice_runtime === 'mio_v2' && !selectedIsThirdParty" class="voice-cloud-panel voice-simple-import">
            <label class="voice-profile-wide"><span>音色名称</span><input v-model.trim="selectedProfile.name" type="text" maxlength="80" /></label>
            <p class="settings-note">仅支持配套 V2 权重，每份最多 2 GiB。请使用可信模型。</p>
            <div class="voice-file-field"><span>GPT 训练文件（.ckpt）</span><div class="voice-file-value"><span class="voice-current-file" :title="selectedProfile.gpt_sovits_gpt_weights">{{ fileName(selectedProfile.gpt_sovits_gpt_weights) }}</span><label class="voice-file-picker">选择文件<input type="file" aria-label="GPT 训练文件（.ckpt）" accept=".ckpt" :disabled="busy" @change="emit('upload-weight', $event, selectedProfileId, 'gpt')" /></label></div></div>
            <div class="voice-file-field"><span>SoVITS 训练文件（.pth）</span><div class="voice-file-value"><span class="voice-current-file" :title="selectedProfile.gpt_sovits_sovits_weights">{{ fileName(selectedProfile.gpt_sovits_sovits_weights) }}</span><label class="voice-file-picker">选择文件<input type="file" aria-label="SoVITS 训练文件（.pth）" accept=".pth" :disabled="busy" @change="emit('upload-weight', $event, selectedProfileId, 'sovits')" /></label></div></div>
            <div class="voice-file-field"><span>参考音频（最多 40 MB）</span><div class="voice-file-value"><span class="voice-current-file" :title="selectedProfile.gpt_sovits_ref_audio">{{ fileName(selectedProfile.gpt_sovits_ref_audio) }}</span><label class="voice-file-picker">选择文件<input type="file" aria-label="参考音频（最多 40 MB）" accept="audio/*,.wav,.mp3,.flac,.m4a,.ogg,.aac,.wma" :disabled="busy" @change="emit('upload-reference', $event, selectedProfileId)" /></label></div></div>
            <label class="voice-profile-wide"><span>参考音频语言</span><SelectField v-model="selectedProfile.gpt_sovits_prompt_language"><option value="zh">中文</option><option value="ja">日语</option><option value="en">英语</option></SelectField></label>
            <label class="voice-profile-wide"><span>参考音频原文</span><textarea v-model.trim="selectedProfile.gpt_sovits_prompt_text" rows="3" maxlength="1000" placeholder="与参考录音逐字一致" /></label>
          </div>
          <div v-if="settings.local_voice_runtime === 'mio_v2' && !selectedIsThirdParty" class="voice-load-row">
            <p v-if="missingVoiceFields.length" class="settings-note" role="status">待补充：{{ missingVoiceFields.join('、') }}</p>
            <button type="button" :disabled="busy || missingVoiceFields.length > 0" @click="startSelectedVoice">保存并加载所选音色</button>
          </div>
          <details class="voice-advanced-editor">
            <summary>音色详情与高级编辑</summary>
            <div class="settings-form-grid voice-profile-form">
              <label v-if="settings.local_voice_runtime !== 'mio_v2' || selectedIsThirdParty"><span>显示名称</span><input v-model.trim="selectedProfile.name" type="text" maxlength="80" /></label>
              <template v-if="selectedIsThirdParty">
                <label><span>引擎</span><input type="text" value="So-VITS-SVC 4.1（基础 TTS → 音色转换）" readonly /></label>
                <label><span>说话人</span><input v-model.trim="selectedProfile.so_vits_svc_speaker" type="text" maxlength="80" /></label>
                <label><span>基础 TTS 音色</span><SelectField v-model="selectedProfile.so_vits_svc_base_profile_id"><option v-for="([id, profile]) in baseProfileEntries" :key="id" :value="id">{{ profile.name || id }}</option></SelectField></label>
                <label><span>音高</span><input v-model.number="selectedProfile.so_vits_svc_pitch" type="range" min="-12" max="12" step="1" :style="rangeInputStyle(selectedProfile.so_vits_svc_pitch, -12, 12)" /><small>{{ selectedProfile.so_vits_svc_pitch || 0 }} 半音</small></label>
                <label class="toggle-row"><input v-model="selectedProfile.so_vits_svc_auto_predict_f0" type="checkbox" /><span>语音自动预测音高</span></label>
                <label class="voice-profile-wide"><span>主模型</span><input :value="selectedProfile.so_vits_svc_model_path" type="text" readonly /></label>
                <label class="voice-profile-wide"><span>来源与许可</span><textarea :value="`${selectedProfile.source_package_name || '未知包'}，${selectedProfile.source_license || '未声明'}`" rows="2" readonly /></label>
              </template>
              <template v-else>
                <label v-if="settings.local_voice_runtime !== 'mio_v2'"><span>参考音频原文语言</span><SelectField v-model="selectedProfile.gpt_sovits_prompt_language"><option value="zh">中文</option><option value="ja">日语</option><option value="en">英语</option><option value="yue">粤语</option></SelectField></label>
                <label v-if="settings.local_voice_runtime !== 'mio_v2'" class="voice-profile-wide"><span>参考音频准确原文</span><textarea v-model.trim="selectedProfile.gpt_sovits_prompt_text" rows="3" maxlength="1000" placeholder="必须与参考音频逐字一致" /></label>
                <template v-if="settings.local_voice_runtime === 'genie' && runtime.local_voice_runtime === 'genie'">
                  <label class="voice-profile-wide"><span>当前 Mio 音色模型</span><input :value="runtime.model_dir || '尚未安装 Mio 本地原声音色'" type="text" readonly /><small>{{ runtime.model_ready ? 'GPT-SoVITS V2 已转换为 Genie ONNX，中文和日语都使用这套角色音色' : '模型文件不完整，请在环境与模型中心重新安装 Mio 本地原声音色' }}</small></label>
                </template>
                <template v-else-if="settings.local_voice_runtime === 'mio_v2'">
                  <p class="settings-note">CUDA FP16，32 token 流式。修改路径后需重新加载。</p>
                  <label class="voice-profile-wide"><span>服务地址</span><input v-model.trim="settings.gpt_sovits_url" type="url" placeholder="http://127.0.0.1:9880" /></label>
                  <label class="voice-profile-wide"><span>GPT 权重完整路径（Mio 所在电脑）</span><input v-model.trim="selectedProfile.gpt_sovits_gpt_weights" type="text" placeholder="选择 .ckpt 文件，或填写完整路径" /></label>

                  <label class="voice-profile-wide"><span>SoVITS 权重完整路径（Mio 所在电脑）</span><input v-model.trim="selectedProfile.gpt_sovits_sovits_weights" type="text" placeholder="选择 .pth 文件，或填写完整路径" /></label>
                  <label class="voice-profile-wide"><span>参考音频完整路径（Mio 所在电脑）</span><input v-model.trim="selectedProfile.gpt_sovits_ref_audio" type="text" /></label>

                </template>
                <template v-else>
                  <label><span>GPT 权重</span><SelectField v-model="selectedProfile.gpt_sovits_gpt_weights"><option value="">未选择</option><option v-for="item in runtime.weights?.gpt || []" :key="item.path" :value="item.path">{{ item.name }}</option></SelectField></label>
                  <label><span>SoVITS 权重</span><SelectField v-model="selectedProfile.gpt_sovits_sovits_weights"><option value="">未选择</option><option v-for="item in runtime.weights?.sovits || []" :key="item.path" :value="item.path">{{ item.name }}</option></SelectField></label>
                </template>
                <label v-if="settings.local_voice_runtime !== 'mio_v2'" class="toggle-row"><input v-model="selectedProfile.use_emotion_references" type="checkbox" /><span>使用可用的情绪参考音频</span></label>
                <label v-if="settings.local_voice_runtime !== 'mio_v2'" class="voice-reference-upload">
                <span>参考音频</span>
                <small>{{ selectedProfile.gpt_sovits_ref_audio || '选择 40 MB 以内的 WAV、MP3、FLAC 等音频' }}</small>
                <span><Upload :size="14" />选择音频<input type="file" accept="audio/*,.wav,.mp3,.flac,.m4a,.ogg,.aac,.wma" @change="emit('upload-reference', $event, selectedProfileId)" /></span>
                </label>
              </template>
            </div>
          </details>
        </div>
      </div>
      <div v-else class="settings-empty-state">尚无音色，可导入音色包或配置云端语音</div>
    </template>

    <div class="settings-list voice-general-settings">
      <label class="settings-item"><span><strong>回复时朗读</strong></span><span class="switch-control"><input v-model="settings.voice_enabled" type="checkbox" /><i /></span></label>
      <label class="settings-item"><span><strong>音量</strong><small>{{ settings.voice_volume }}%</small></span><input v-model.number="settings.voice_volume" type="range" min="0" max="100" :style="rangeInputStyle(settings.voice_volume, 0, 100)" /></label>
      <label v-if="engine !== 'indextts2'" class="settings-item"><span><strong>桌宠开口语言</strong></span><SelectField v-model="settings.pet_speech_language"><option value="zh">中文</option><option value="ja">日语</option></SelectField></label>
    </div>
    <details class="voice-advanced-settings"><summary>更多朗读设置与运行状态</summary>
      <div class="settings-list">
      <label v-if="engine === 'gpt_sovits'" class="settings-item"><span><strong>打开 Mio 时启动音色引擎</strong><small>提前加载，首次说话等待更短</small></span><span class="switch-control"><input v-model="settings.voice_startup_enabled" type="checkbox" /><i /></span></label>
      <label v-if="engine === 'gpt_sovits'" class="settings-item"><span><strong>空闲后释放音色内存</strong><small>释放内存后，下次说话需重新加载</small></span><SelectField v-model.number="settings.voice_idle_timeout_seconds"><option :value="60">1 分钟</option><option :value="180">3 分钟（推荐）</option><option :value="600">10 分钟</option><option :value="0">始终保留，优先速度</option></SelectField></label>
      <label class="settings-item"><span><strong>主动消息也朗读</strong><small>屏幕观察和主动联系产生的消息可以直接出声</small></span><span class="switch-control"><input v-model="settings.speak_proactive" type="checkbox" /><i /></span></label>
      <label v-if="engine !== 'indextts2'" class="settings-item"><span><strong>流式语音</strong><small>分段合成并尽快开始播放，降低等待时间（本地音色）</small></span><span class="switch-control"><input v-model="settings.voice_streaming_enabled" type="checkbox" /><i /></span></label>
      <label class="settings-item"><span><strong>QQ 语音</strong><small>群聊和私聊使用同一套判断规则</small></span><SelectField v-model="settings.qq_voice_mode"><option value="explicit">仅明确要求</option><option value="adaptive">由 Agent 判断</option><option value="always">始终语音</option></SelectField></label>
      <label v-if="engine !== 'indextts2'" class="settings-item"><span><strong>语音翻译模型</strong><small>只负责中日语音翻译，不跟随主对话或桌宠模型</small></span><SelectField v-model="settings.speech_translation_model_id" :disabled="!modelOptions.length"><option v-if="translationModelUnavailable" :value="settings.speech_translation_model_id" disabled>{{ modelOptions.length ? '当前模型未连接，请重新选择' : '没有已连接的模型' }}</option><option v-else-if="!settings.speech_translation_model_id" value="" disabled>{{ modelOptions.length ? '请选择已连接的模型' : '没有已连接的模型' }}</option><option v-for="model in modelOptions" :key="model.id" :value="model.id">{{ model.display_name || model.model }}</option></SelectField></label>
      </div>
    <template v-if="engine === 'gpt_sovits'">
      <p class="settings-note">
        预热：{{ { idle: '未开始', scheduled: '等待中', running: '进行中', ready: '已就绪', failed: '失败' }[runtime.warmup_state] || '未开始' }}
        <template v-if="runtime.warmup_seconds != null">，{{ Number(runtime.warmup_seconds).toFixed(2) }} 秒</template>
        <template v-if="runtime.last_first_audio_ms != null">，最近首音频 {{ Number(runtime.last_first_audio_ms).toFixed(0) }} 毫秒</template>
        <template v-if="runtime.warmup_error">，{{ runtime.warmup_error }}</template>
      </p>
    </template>
    </details>
    <div v-if="engine === 'gpt_sovits'" class="settings-actions-row">
      <button v-if="settings.local_voice_runtime !== 'mio_v2'" type="button" :disabled="busy" @click="startSelectedVoice">保存并启动音色服务</button>
      <button type="button" :disabled="busy" @click="emit('control-runtime', 'stop')">停止音色服务</button>
    </div>

    <RealtimeVoiceModal :visible="realtimeVisible" @close="realtimeVisible = false" />
  </div>
</template>

<style scoped>
.voice-settings-panel { font-family: "Microsoft YaHei UI", "Microsoft YaHei", system-ui, sans-serif; font-size: var(--text-base); }
.voice-settings-panel h2 { font: inherit; font-size: var(--text-lg); font-weight: 600; }
.voice-settings-panel :is(button, input, textarea, summary, label, strong) { font-family: inherit; font-size: var(--text-base); }
.voice-settings-panel :is(button, input, textarea) { box-shadow: none; }
.voice-settings-panel :is(small, .settings-note, .voice-import-hint, .connection-label) { font-family: inherit; font-size: var(--text-xs); line-height: 1.6; }
.voice-settings-panel .settings-note { margin: 0; padding: 0; color: var(--text-muted); background: transparent; border: 0; border-radius: 0; }
.voice-settings-panel .connection-label { padding: 0; background: transparent; border: 0; box-shadow: none; }
.voice-engine-choice { display: flex; flex-wrap: wrap; gap: 4px 16px; border-bottom: 1px solid var(--border); }
.voice-engine-choice > button { min-height: 40px; padding: 6px 0; color: var(--text-muted); background: transparent; border: 0; border-bottom: 2px solid transparent; border-radius: 0; box-shadow: none; font-weight: 400; }
.voice-engine-choice > button.active { color: var(--text); border-color: var(--accent); box-shadow: none; font-weight: 600; }
.voice-cloud-panel { gap: 12px; margin: 0; padding: 12px 0; background: transparent; border: 0; border-radius: 0; box-shadow: none; }
.voice-cloud-panel > .voice-profile-wide { display: grid; min-width: 0; gap: 7px; }
.voice-cloud-panel > .voice-profile-wide > textarea { width: 100%; }
.voice-cloud-panel .settings-form-grid > .voice-profile-wide { grid-column: 1 / -1; }
.voice-model-guide { display: grid; gap: 12px; }
.voice-model-help > p { margin-top: 8px; }
.voice-model-links { display: flex; flex-wrap: wrap; gap: 6px 16px; }
.voice-model-links a { font-size: var(--text-xs); overflow-wrap: anywhere; }
.voice-selection-row { display: flex; align-items: end; flex-wrap: wrap; gap: 12px; }
.voice-selection-field { display: grid; gap: 7px; flex: 1 1 260px; min-width: 0; }
.voice-selection-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.voice-settings-panel :is(.voice-selection-actions, .voice-new-profile, .voice-load-row) button { min-height: 36px; padding: 6px 12px; border: 1px solid var(--border-strong); border-radius: 6px; color: var(--text); background: var(--surface-raised); font: inherit; cursor: pointer; }
.voice-settings-panel :is(.voice-selection-actions, .voice-new-profile, .voice-load-row) button:disabled { color: var(--text-muted); opacity: .5; cursor: default; }
.voice-create-profile { padding: 0; }
.voice-new-profile { display: flex; align-items: end; flex-wrap: wrap; gap: 10px; margin-top: 12px; }
.voice-new-profile label { display: grid; gap: 7px; flex: 1 1 220px; min-width: 0; }
.voice-package-tools { margin: 0; }
.voice-import-row { padding: 12px 0 0; }
.voice-import-hint { flex-basis: 100%; }
.voice-settings-panel .voice-profile-layout { display: block; min-width: 0; }
.voice-profile-editor { padding: 0; background: transparent; border: 0; border-radius: 0; box-shadow: none; }
.voice-file-field { display: grid; grid-template-columns: 180px minmax(0, 1fr); align-items: center; gap: 12px; min-width: 0; }
.voice-file-value { display: flex; align-items: center; gap: 12px; min-width: 0; }
.voice-current-file { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-muted); }
.voice-file-picker { position: relative; display: inline-flex; align-items: center; justify-content: center; flex: none; min-height: 36px; padding: 6px 12px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--surface-raised); cursor: pointer; }
.voice-file-picker:focus-within { outline: 2px solid var(--accent); outline-offset: 2px; }
.voice-file-picker input[type="file"] { position: absolute; inset: 0; width: 100%; height: 100%; opacity: 0; cursor: pointer; }
.voice-file-picker:has(input:disabled) { opacity: .5; cursor: default; }
.voice-load-row { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; padding: 4px 0 12px; }
.voice-load-row button { margin-left: auto; }
.voice-simple-import { display: grid; gap: 14px; }
.voice-simple-import input[type="file"], .voice-profile-form input[type="file"] { min-width: 0; max-width: 100%; }
.voice-profile-form input[type="text"] { min-width: 0; }
.voice-advanced-editor { padding: 12px 0 0; }
.voice-settings-panel :is(.voice-model-help, .voice-package-tools, .voice-create-profile, .voice-advanced-editor) { border: 0; border-radius: 0; background: transparent; box-shadow: none; }
.voice-advanced-editor > div { margin-top: 12px; }
.voice-settings-panel .settings-list { background: transparent; border: 0; border-radius: 0; box-shadow: none; }
.voice-settings-panel .settings-item { padding-inline: 0; background: transparent; border-radius: 0; }
.voice-advanced-settings { padding: 12px 0; border-top: 1px solid var(--border); }
@media (max-width: 600px) {
  .voice-file-field { grid-template-columns: minmax(0, 1fr); gap: 7px; }
  .voice-selection-field { flex-basis: 100%; }
  .voice-selection-actions { width: 100%; justify-content: flex-end; }
  .voice-load-row button { width: 100%; margin-left: 0; }
  .voice-engine-choice { gap: 2px 12px; }
}
</style>
