<script setup>
import { computed, ref, watch } from 'vue'
import { Check, Cloud, Download, Eraser, KeyRound, PhoneCall, Play, Power, Trash2, Upload, Volume2 } from '@lucide/vue'
import { removeVoiceProfile } from '../voiceProfiles.js'
import RealtimeVoiceModal from './RealtimeVoiceModal.vue'

const props = defineProps({
  status: { type: Object, required: true },
  modelOptions: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
  rangeInputStyle: { type: Function, required: true },
})

const emit = defineEmits(['control-runtime', 'test', 'upload-reference', 'export-package', 'import-package'])
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
const cloudApiKeyDraft = ref('')
const realtimeVisible = ref(false)
const settings = computed(() => props.status?.pet?.settings || {})
const runtime = computed(() => props.status?.voice_runtime || {})
const cloudState = computed(() => runtime.value.cloud_tts || {})
const engine = computed(() => (settings.value.voice_engine === 'cloud' ? 'cloud' : 'gpt_sovits'))
const profileEntries = computed(() => Object.entries(settings.value.voice_profiles || {}))
const selectedProfile = computed(() => (
  settings.value.voice_profiles?.[selectedProfileId.value] || profileEntries.value[0]?.[1] || null
))
const selectedIsDefault = computed(() => selectedProfileId.value === settings.value.default_voice_profile_id)
const selectedIsThirdParty = computed(() => selectedProfile.value?.engine === 'so_vits_svc')
const baseProfileEntries = computed(() => profileEntries.value.filter(([, profile]) => profile.engine !== 'so_vits_svc'))

function profileSubtitle(profile) {
  if (profile?.engine === 'so_vits_svc') return `So-VITS-SVC · ${profile.so_vits_svc_speaker || '单说话人'}`
  return profile?.gpt_sovits_ref_audio ? 'GPT-SoVITS V2 · Genie ONNX' : 'GPT-SoVITS V2 · 尚未上传参考音频'
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
  if (engine.value === 'cloud') {
    settings.value.cloud_tts_api_key = cloudApiKeyDraft.value.trim()
    emit('test', '')
  } else {
    makeSelectedDefault()
    emit('test', selectedProfileId.value)
  }
}

function clearCloudKey() {
  cloudApiKeyDraft.value = ''
  settings.value.cloud_tts_api_key = '__clear__'
}

function openRealtime() {
  if (engine.value !== 'cloud' || !cloudState.value.configured) return
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

function makeSelectedDefault() {
  if (selectedProfileId.value) settings.value.default_voice_profile_id = selectedProfileId.value
}
</script>

<template>
  <div class="settings-section-block voice-settings-panel">
    <div class="settings-block-heading">
      <div>
        <h2>角色语音</h2>
        <p>选一条路让角色开口：云端语音填一个 Key 就能用；本地音色用导入的音色包合成</p>
      </div>
      <span v-if="engine === 'cloud'" :class="['connection-label', { online: cloudState.configured }]">
        {{ cloudState.configured ? '云端已配置' : '云端未配置' }}
      </span>
      <span v-else :class="['connection-label', { online: runtime.service_running }]">
        {{ runtime.service_running ? '服务在线' : '未启动' }}
      </span>
    </div>

    <div class="voice-engine-choice">
      <button type="button" :class="{ active: engine === 'cloud' }" @click="selectEngine('cloud')">
        <Cloud :size="17" />
        <span><strong>云端语音</strong><small>填豆包 API Key，不用下载、不用显卡，回复立即能朗读</small></span>
        <Check v-if="engine === 'cloud'" :size="14" />
      </button>
      <button type="button" :class="{ active: engine === 'gpt_sovits' }" @click="selectEngine('gpt_sovits')">
        <Volume2 :size="17" />
        <span><strong>本地音色</strong><small>GPT-SoVITS V2 音色由 Genie ONNX 在 CPU 上合成</small></span>
        <Check v-if="engine === 'gpt_sovits'" :size="14" />
      </button>
    </div>

    <div v-if="engine === 'cloud'" class="voice-cloud-panel">
      <div class="cloud-tts-guide">
        <p>去哪里拿 Key：打开<a href="https://console.volcengine.com/speech/new/setting/apikeys" target="_blank" rel="noopener">火山引擎语音控制台</a>，登录后「新建 API Key」复制到这里即可。填完点「试听」，出声就说明成功。</p>
      </div>
      <div class="settings-form-grid cloud-tts-form">
        <label class="voice-cloud-key-field">
          <span>语音 API Key</span>
          <small>{{ cloudState.configured ? '已经保存了一个 Key；留空保存表示不修改' : '填写后加密保存在当前 Windows 用户下' }}</small>
          <input v-model="cloudApiKeyDraft" type="password" autocomplete="new-password" :placeholder="cloudState.configured ? '已配置（留空不改）' : '粘贴火山引擎语音 API Key'" />
        </label>
        <label>
          <span>音色</span>
          <select v-model="settings.cloud_tts_speaker">
            <option v-for="item in CLOUD_SPEAKERS" :key="item.id" :value="item.id">{{ item.name }}</option>
          </select>
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
      <p class="settings-note">云端语音需要联网；Key 只在合成时发送给火山引擎，不会写入备份、日志或导出文件。实时对话像打电话一样边听边答、可以随时打断，同样使用这个 Key。</p>
    </div>

    <template v-else>
      <div class="voice-import-row">
        <button type="button" class="voice-package-import voice-package-import-primary" :disabled="busy" @click="startVoicePackageImport">
          <Upload :size="15" />导入音色包
        </button>
        <input ref="voicePackageInput" class="voice-package-file-input" type="file" accept=".zip,application/zip" :disabled="busy" @change="emit('import-package', $event)" />
        <button type="button" :disabled="busy || !selectedProfileId || selectedIsThirdParty" :title="selectedIsThirdParty ? '第三方模型不重复导出，请保留原始模型包' : ''" @click="emit('export-package', selectedProfileId)">
          <Download :size="15" />导出音色包
        </button>
        <span class="voice-import-hint">支持 Mio 交换音色包，以及带 config.json + G_*.pth 的 So-VITS-SVC 4.1 模型包；第三方模型只导入可信来源</span>
      </div>

      <div v-if="profileEntries.length" class="voice-profile-layout">
        <nav class="voice-profile-list" aria-label="音色列表">
          <button
            v-for="([id, profile]) in profileEntries"
            :key="id"
            type="button"
            :class="{ active: selectedProfileId === id }"
            @click="selectedProfileId = id"
          >
            <span><strong>{{ profile.name || id }}</strong><small>{{ profileSubtitle(profile) }}</small></span>
            <Check v-if="settings.default_voice_profile_id === id" :size="15" aria-label="默认音色" />
          </button>
        </nav>

        <div v-if="selectedProfile" class="voice-profile-editor">
          <div class="voice-profile-heading">
            <div><strong>{{ selectedProfile.name || selectedProfileId }}</strong><small>{{ selectedIsDefault ? '当前默认音色' : '可设为默认音色' }}</small></div>
            <span>
              <button type="button" :disabled="selectedIsDefault" @click="makeSelectedDefault"><Check :size="14" />设为默认</button>
              <button type="button" :disabled="busy || !selectedProfile" @click="testCurrentEngine"><Volume2 :size="14" />试听</button>
              <button class="danger-button" type="button" :disabled="profileEntries.length <= 1" @click="deleteSelectedProfile"><Trash2 :size="14" />删除</button>
            </span>
          </div>

          <details class="voice-advanced-editor">
            <summary>音色详情与高级编辑</summary>
            <div class="settings-form-grid voice-profile-form">
              <label><span>显示名称</span><input v-model.trim="selectedProfile.name" type="text" maxlength="80" /></label>
              <template v-if="selectedIsThirdParty">
                <label><span>引擎</span><input type="text" value="So-VITS-SVC 4.1（基础 TTS → 音色转换）" readonly /></label>
                <label><span>说话人</span><input v-model.trim="selectedProfile.so_vits_svc_speaker" type="text" maxlength="80" /></label>
                <label><span>基础 TTS 音色</span><select v-model="selectedProfile.so_vits_svc_base_profile_id"><option v-for="([id, profile]) in baseProfileEntries" :key="id" :value="id">{{ profile.name || id }}</option></select></label>
                <label><span>音高</span><input v-model.number="selectedProfile.so_vits_svc_pitch" type="range" min="-12" max="12" step="1" :style="rangeInputStyle(selectedProfile.so_vits_svc_pitch, -12, 12)" /><small>{{ selectedProfile.so_vits_svc_pitch || 0 }} 半音</small></label>
                <label class="toggle-row"><input v-model="selectedProfile.so_vits_svc_auto_predict_f0" type="checkbox" /><span>语音自动预测音高</span></label>
                <label class="voice-profile-wide"><span>主模型</span><input :value="selectedProfile.so_vits_svc_model_path" type="text" readonly /></label>
                <label class="voice-profile-wide"><span>来源与许可</span><textarea :value="`${selectedProfile.source_package_name || '未知包'} · ${selectedProfile.source_license || '未声明'}`" rows="2" readonly /></label>
              </template>
              <template v-else>
                <label><span>参考音频原文语言</span><select v-model="selectedProfile.gpt_sovits_prompt_language"><option value="zh">中文</option><option value="ja">日语</option><option value="en">英语</option><option value="yue">粤语</option></select></label>
                <label class="voice-profile-wide"><span>参考音频准确原文</span><textarea v-model.trim="selectedProfile.gpt_sovits_prompt_text" rows="3" maxlength="1000" placeholder="必须与参考音频逐字一致" /></label>
                <template v-if="runtime.local_voice_runtime === 'genie'">
                  <label class="voice-profile-wide"><span>当前 Mio 音色模型</span><input :value="runtime.model_dir || '尚未安装 Mio 本地原声音色'" type="text" readonly /><small>{{ runtime.model_ready ? 'GPT-SoVITS V2 已转换为 Genie ONNX，中文和日语都使用这套角色音色' : '模型文件不完整，请在环境与模型中心重新安装 Mio 本地原声音色' }}</small></label>
                </template>
                <template v-else>
                  <label><span>GPT 权重</span><select v-model="selectedProfile.gpt_sovits_gpt_weights"><option value="">未选择</option><option v-for="item in runtime.weights?.gpt || []" :key="item.path" :value="item.path">{{ item.name }}</option></select></label>
                  <label><span>SoVITS 权重</span><select v-model="selectedProfile.gpt_sovits_sovits_weights"><option value="">未选择</option><option v-for="item in runtime.weights?.sovits || []" :key="item.path" :value="item.path">{{ item.name }}</option></select></label>
                </template>
                <label class="toggle-row"><input v-model="selectedProfile.use_emotion_references" type="checkbox" /><span>使用可用的情绪参考音频</span></label>
                <label class="voice-reference-upload">
                <span>参考音频</span>
                <small>{{ selectedProfile.gpt_sovits_ref_audio || '选择 40 MB 以内的 WAV、MP3、FLAC 等音频' }}</small>
                <span><Upload :size="14" />选择音频<input type="file" accept="audio/*,.wav,.mp3,.flac,.m4a,.ogg,.aac,.wma" @change="emit('upload-reference', $event, selectedProfileId)" /></span>
                </label>
              </template>
            </div>
          </details>
        </div>
      </div>
      <div v-else class="settings-empty-state">还没有导入音色包，点上方「导入音色包」开始；也可以先切到「云端语音」填一个 Key 就出声</div>
    </template>

    <div class="settings-list voice-general-settings">
      <label class="settings-item"><span><strong>回复时朗读</strong><small>桌宠与主应用可播放模型回复</small></span><span class="switch-control"><input v-model="settings.voice_enabled" type="checkbox" /><i /></span></label>
      <label v-if="engine === 'gpt_sovits'" class="settings-item"><span><strong>打开 Mio 时启动音色引擎</strong><small>启动后在后台预热本地音色；关闭时仍会在第一次试听或朗读时按需启动</small></span><span class="switch-control"><input v-model="settings.voice_startup_enabled" type="checkbox" /><i /></span></label>
      <label v-if="engine === 'gpt_sovits'" class="settings-item"><span><strong>空闲后释放音色内存</strong><small>释放后可回收约数 GB 内存；下一次说话需要重新加载一次</small></span><select v-model.number="settings.voice_idle_timeout_seconds"><option :value="60">1 分钟</option><option :value="180">3 分钟（推荐）</option><option :value="600">10 分钟</option><option :value="0">始终保留，优先速度</option></select></label>
      <label class="settings-item"><span><strong>主动消息也朗读</strong><small>屏幕观察和主动联系产生的消息可以直接出声</small></span><span class="switch-control"><input v-model="settings.speak_proactive" type="checkbox" /><i /></span></label>
      <label class="settings-item"><span><strong>流式语音</strong><small>分段合成并尽快开始播放，降低等待时间（本地音色）</small></span><span class="switch-control"><input v-model="settings.voice_streaming_enabled" type="checkbox" /><i /></span></label>
      <label class="settings-item"><span><strong>QQ 语音</strong><small>群聊和私聊使用同一套判断规则</small></span><select v-model="settings.qq_voice_mode"><option value="explicit">仅明确要求</option><option value="adaptive">由 Agent 判断</option><option value="always">始终语音</option></select></label>
      <label class="settings-item"><span><strong>音量</strong><small>{{ settings.voice_volume }}%</small></span><input v-model.number="settings.voice_volume" type="range" min="0" max="100" :style="rangeInputStyle(settings.voice_volume, 0, 100)" /></label>
      <label class="settings-item"><span><strong>桌宠开口语言</strong><small>控制朗读使用中文还是日语，气泡继续显示原回复</small></span><select v-model="settings.pet_speech_language"><option value="zh">中文</option><option value="ja">日语</option></select></label>
      <label class="settings-item"><span><strong>语音翻译模型</strong><small>只负责中日语音翻译，不跟随主对话或桌宠模型</small></span><select v-model="settings.speech_translation_model_id"><option v-for="model in modelOptions" :key="model.id" :value="model.id">{{ model.display_name || model.model }}</option></select></label>
    </div>

    <template v-if="engine === 'gpt_sovits'">
      <p class="settings-note">
        预热：{{ { idle: '未开始', scheduled: '等待中', running: '进行中', ready: '已就绪', failed: '失败' }[runtime.warmup_state] || '未开始' }}
        <template v-if="runtime.warmup_seconds != null"> · {{ Number(runtime.warmup_seconds).toFixed(2) }} 秒</template>
        <template v-if="runtime.last_first_audio_ms != null"> · 最近首音频 {{ Number(runtime.last_first_audio_ms).toFixed(0) }} 毫秒</template>
        <template v-if="runtime.warmup_error"> · {{ runtime.warmup_error }}</template>
      </p>
      <div class="settings-actions-row">
        <button type="button" :disabled="busy" @click="emit('control-runtime', 'start')"><Play :size="14" />启动音色服务</button>
        <button type="button" :disabled="busy" @click="emit('control-runtime', 'stop')"><Power :size="14" />停止</button>
      </div>
    </template>

    <RealtimeVoiceModal :visible="realtimeVisible" @close="realtimeVisible = false" />
  </div>
</template>
