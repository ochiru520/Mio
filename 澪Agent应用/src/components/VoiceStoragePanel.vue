<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { loadManagedVoices, previewVoiceRemoval, removeManagedVoice, selectManagedVoice } from '../services/modelManagementApi.js'
import { formatModelBytes } from '../modelCenterState.js'
const emit = defineEmits(['refresh-environment', 'voices-changed', 'navigate', 'manage-anif'])
const voices = ref([]), selected = ref(null), deleteFiles = ref(false), removeProfile = ref(true), plan = ref(null), busy = ref(false), loading = ref(false), error = ref(''), notice = ref(''), remaining = ref([]), panel = ref(null)
const languageLabel = language => ({ zh: '中文', ja: '日语', en: '英语', auto: '自动识别' })[language] || language
const uncertainResult = cause => ['request_timeout', 'request_cancelled', 'network_error'].includes(cause?.code) || cause?.status >= 500
let revision = 0, readId = 0, disposed = false
async function refresh(silent = false) {
  const version = revision, request = ++readId
  loading.value = true
  if (!silent) error.value = ''
  try {
    const result = await loadManagedVoices()
    if (disposed || version !== revision || request !== readId) return
    if (result.selection_in_progress) { error.value ||= '正在加载并验证音色，请完成后重新检查；当前显示上次确认的结果。'; return }
    voices.value = result.voices || []
    if (selected.value) selected.value = voices.value.find(voice => voice.id === selected.value.id) || null
  } catch (cause) { if (!disposed && version === revision && request === readId && (!silent || !error.value)) error.value = cause.message || '无法检查音色，请重试。' }
  finally { if (request === readId) loading.value = false }
}
async function operationError(cause) {
  if (disposed) return
  error.value = cause.message || '操作未完成，请重试。'
  if (uncertainResult(cause)) {
    error.value += '。操作结果尚未确认，后台可能仍在处理；请重新检查状态后再操作。'
    selected.value = null; plan.value = null
    await refresh(true); emit('voices-changed'); emit('refresh-environment')
  }
}
async function useVoice(voice, variant) {
  if (busy.value || !variant.can_select) return
  revision++
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const result = await selectManagedVoice(voice.id, variant.engine)
    if (disposed) return
    if (result.ok === false) throw new Error(result.message || '切换未完成，原来的音色选择保留。')
    selected.value = null; plan.value = null
    notice.value = result.message || '音色加载验证通过，已选择这个声音引擎。可以到声音设置试听。'
    await refresh(true); emit('voices-changed'); emit('refresh-environment')
  } catch (cause) { await operationError(cause) }
  finally { busy.value = false }
}
function select(voice) { selected.value = voice; deleteFiles.value = false; removeProfile.value = true; plan.value = null; error.value = ''; notice.value = ''; remaining.value = [] }
async function preview() {
  if (busy.value || !selected.value) return
  if (selected.value.selected) { error.value = '请先切换到其他音色，再移除或清理当前音色。'; return }
  revision++
  busy.value = true; error.value = ''; plan.value = null
  try { plan.value = await previewVoiceRemoval(selected.value.id, { delete_files: deleteFiles.value, remove_profile: removeProfile.value }); await nextTick(); panel.value?.focus({ preventScroll: true }); panel.value?.scrollIntoView({ block: 'nearest' }) }
  catch (cause) { await operationError(cause) }
  finally { busy.value = false }
}
async function confirm() {
  if (!plan.value || busy.value) return
  revision++
  busy.value = true; error.value = ''; remaining.value = []
  const reviewed = plan.value
  try {
    const result = await removeManagedVoice(reviewed.id, { delete_files: reviewed.delete_files, remove_profile: reviewed.remove_profile }, reviewed.token)
    if (disposed) return
    remaining.value = result.remaining_files || []
    const message = `${result.message || '已处理音色。'} 实际释放 ${formatModelBytes(result.removed_bytes)}。`
    if (result.ok === false) error.value = message
    else notice.value = message
    plan.value = null; selected.value = null
    await refresh(true); emit('voices-changed'); emit('refresh-environment')
  } catch (cause) { await operationError(cause) }
  finally { busy.value = false }
}
onMounted(refresh)
onBeforeUnmount(() => { disposed = true; revision++ })
</script>
<template>
  <section class="voice-storage" aria-label="个人音色文件管理">
    <p>升级和更换引擎时保留个人音色。可以选择兼容的运行方式，加载试听后再决定使用；文件齐全不代表已经实际发声验证。</p>
    <p>可分别移除音色配置或独占的原始文件，共用和外部文件保留。请先保存声音设置，切换或清理后会重新载入。</p>
    <button type="button" :disabled="busy || loading" @click="refresh()">{{ loading ? '正在检查音色…' : '重新检查音色' }}</button>
    <p v-if="error" role="alert" class="error">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="busy" role="status">正在处理；切换音色时会加载并验证，失败会保留原选择。</p>
    <ul v-if="remaining.length"><li v-for="path in remaining" :key="path">尚未删除：{{ path }}</li></ul>
    <p v-if="!voices.length && !error">还没有可管理的音色。可以先跳过，之后到声音设置导入。</p>
    <article v-for="voice in voices" :key="voice.id" :aria-label="voice.name"><strong>{{ voice.name }}{{ voice.selected ? '，当前默认音色' : '' }}</strong>
      <p v-if="voice.engine_label">当前引擎：{{ voice.engine_label }}<template v-if="voice.model_version">，权重版本：{{ voice.model_version }}</template></p>
      <p v-if="voice.languages?.length">支持语言：{{ voice.languages.map(languageLabel).join('、') }}</p><p v-if="voice.language_note">{{ voice.language_note }}</p>
      <ul v-if="voice.missing_files?.length" class="error"><li v-for="file in voice.missing_files" :key="file">缺少文件：{{ file }}</li></ul>
      <section v-if="voice.variants?.length" aria-label="音色可用方式"><div v-for="variant in voice.variants" :key="variant.engine" class="variant"><strong>{{ variant.label }}</strong><p>{{ variant.reason }}</p><button v-if="variant.can_select" type="button" :disabled="busy" @click="useVoice(voice, variant)">使用此音色 · {{ variant.label }}</button><button v-if="variant.engine === 'aniflive' && variant.state === 'needs_conversion'" type="button" :disabled="busy" @click="emit('manage-anif')">去安装 Anif / 转换音色</button><p v-if="variant.state === 'incompatible'">此运行方式暂不兼容，请保留原音色或选其他兼容引擎。</p></div></section>
      <p>原始文件可独立清理 {{ formatModelBytes(voice.owned_bytes) }}，共用文件 {{ (voice.shared_files || []).length }} 项，外部文件 {{ (voice.external_files || []).length }} 项</p>
      <p v-if="voice.variants?.some(variant => variant.engine === 'aniflive' && variant.state === 'available')">Anif 加速副本与原始权重分别管理。原件仍被副本引用时不能删除；卸载加速副本请到 Anif 安装面板。</p>
      <details v-if="voice.conversions?.length"><summary>已关联的加速副本</summary><p v-for="copy in voice.conversions" :key="copy.path">{{ copy.managed ? 'Mio 管理的加速副本' : '外部加速副本' }}：{{ copy.cleanup_label }}</p><p>{{ voice.conversion_cleanup }}</p></details>
      <button type="button" :disabled="busy" @click="emit('navigate', 'settings-voice')">去试听 / 导入音色</button><button v-if="voice.variants?.some(variant => variant.engine === 'aniflive')" type="button" :disabled="busy" @click="emit('manage-anif')">管理 Anif 加速副本</button><button type="button" :disabled="busy" @click="select(voice)">管理这个音色</button>
    </article>
    <section v-if="selected" class="options"><strong>管理 {{ selected.name }}</strong><p v-if="selected.selected">请先切换到其他音色，再移除或清理当前音色。</p><label><input v-model="removeProfile" type="checkbox" :disabled="busy" @change="plan = null" />移除音色配置</label><label><input v-model="deleteFiles" type="checkbox" :disabled="busy || !selected.can_delete_files" @change="plan = null" />删除此音色独占的 Mio 管理文件</label><p v-if="!selected.can_delete_files">没有可独立删除的受管文件；外部文件及共用文件不会删除。</p><button type="button" :disabled="busy || (!deleteFiles && !removeProfile)" @click="preview">预览影响与文件</button><button type="button" :disabled="busy" @click="selected = null; plan = null">取消</button></section>
    <section v-if="plan" ref="panel" class="confirm" tabindex="-1" aria-label="确认清理个人音色"><p>{{ plan.impact }}</p><p>{{ plan.preserved }}</p><p>预计释放 {{ formatModelBytes(plan.size_bytes) }}</p><ul><li v-for="path in plan.paths" :key="path">将删除：{{ path }}</li><li v-for="path in plan.shared_files" :key="path">保留共用文件：{{ path }}</li><li v-for="path in plan.external_files" :key="path">保留外部文件：{{ path }}</li></ul><button type="button" :disabled="busy || !plan.token" @click="confirm">{{ busy ? '正在处理…' : '确认音色清理' }}</button><button type="button" :disabled="busy" @click="plan = null">返回修改</button></section>
  </section>
</template>
<style scoped>
.voice-storage { font-size: var(--text-base); line-height:1.7; overflow-wrap:anywhere; }
.voice-storage article,.voice-storage .options,.voice-storage .confirm { border:0; border-top:1px solid var(--line); padding:12px 0; margin:10px 0; }
.voice-storage button { font:inherit; color:inherit; background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:6px 10px; cursor:pointer; margin:0 8px 6px 0; }
.voice-storage button:disabled { opacity:.5; cursor:wait; }
.voice-storage label { display:block; margin:10px 0; }
.voice-storage .error { color:var(--danger); }
.voice-storage .variant { border-left:2px solid var(--line); padding-left:12px; margin:10px 0; }
.voice-storage button { max-width:100%; white-space:normal; }
</style>
