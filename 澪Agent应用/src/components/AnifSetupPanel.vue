<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import * as anifApi from '../services/anifSetupApi.js'
import { formatModelBytes } from '../modelCenterState.js'

const emit = defineEmits(['changed', 'navigate'])
const opened = ref(false), loading = ref(false), busy = ref(false)
const snapshot = ref(null), voiceId = ref(''), error = ref(''), notice = ref('')
const removal = ref(null), audioUrls = ref({}), loadingAudio = ref('')
const job = computed(() => snapshot.value?.job || {})
const running = computed(() => job.value.running === true)
const voices = computed(() => snapshot.value?.voices || [])
const selectedVoice = computed(() => voices.value.find(voice => voice.id === voiceId.value))
const ready = computed(() => job.value.stage === 'ready' && job.value.ready === true && !running.value)
const preparedVoiceName = computed(() => voices.value.find(voice => voice.id === job.value.voice_id)?.name || job.value.voice_id || '已编译音色')
const canStart = computed(() => snapshot.value?.can_start === true && selectedVoice.value?.compatible !== false && Boolean(selectedVoice.value) && !running.value)
const canActivate = computed(() => snapshot.value?.can_activate === true || (snapshot.value?.can_activate == null && ready.value))
const canUninstall = computed(() => snapshot.value?.can_uninstall === true || (snapshot.value?.can_uninstall == null && Boolean(snapshot.value?.installed)))
const progress = computed(() => Math.min(100, Math.max(0, Number(job.value.percent) || 0)))
const downloadProgress = computed(() => {
  const total = Number(job.value.total_bytes)
  if (job.value.stage !== 'downloading' || !Number.isFinite(total) || total <= 0) return ''
  const downloaded = Math.min(total, Math.max(0, Number(job.value.downloaded_bytes) || 0))
  return `已下载 ${formatModelBytes(downloaded)} / ${formatModelBytes(total)}`
})
const active = computed(() => (snapshot.value?.selected === true || snapshot.value?.installed?.active === true) && snapshot.value?.installed?.job_id === job.value.id)
const failed = computed(() => ['failed', 'error', 'interrupted'].includes(job.value.stage))
const steps = [{ id: 'download', label: '准备运行环境' }, { id: 'build', label: '为这台电脑编译音色' }, { id: 'verify', label: '检查中文和日语发声' }, { id: 'ready', label: '试听后选择使用' }]
let timer, disposed = false, audioGeneration = 0

function clearAudio() {
  audioGeneration++
  for (const url of Object.values(audioUrls.value)) URL.revokeObjectURL(url)
  audioUrls.value = {}
  loadingAudio.value = ''
}

function schedule(delay = 1800) {
  clearTimeout(timer)
  if (!disposed && running.value) timer = setTimeout(() => refresh(true), delay)
}

async function refresh(silent = false) {
  if (loading.value || disposed) return
  loading.value = true
  if (!silent) error.value = ''
  try {
    const result = await anifApi.loadAnifSetup()
    if (disposed) return
    if (result.job?.id !== job.value.id) clearAudio()
    snapshot.value = result
    if (!voices.value.some(voice => voice.id === voiceId.value)) voiceId.value = voices.value.find(voice => voice.compatible !== false)?.id || ''
    schedule()
  } catch (cause) {
    if (!disposed) { error.value = cause.message || '无法检查安装状态，请稍后重试。'; schedule(4000) }
  } finally { loading.value = false }
}

function toggle() { opened.value = !opened.value; if (opened.value) refresh() }

async function act(callback) {
  if (busy.value || disposed) return
  busy.value = true; error.value = ''; notice.value = ''
  try { await callback() }
  catch (cause) { if (!disposed) error.value = cause.message || '操作未完成，请重试。' }
  finally { busy.value = false }
}

async function start() {
  if (!canStart.value) return
  await act(async () => {
    const result = await anifApi.startAnifSetup(voiceId.value)
    if (disposed) return
    clearAudio(); removal.value = null
    if (result.job) snapshot.value = { ...snapshot.value, ...result }
    notice.value = '已开始准备。可以收起此页继续使用 Mio，稍后回来查看进度。'
    schedule()
    await refresh()
  })
}

async function cancel() {
  await act(async () => {
    const result = await anifApi.cancelAnifSetup()
    if (disposed) return
    notice.value = result.message || '已请求取消，正在停止当前操作。'
    await refresh()
  })
}

async function listen(language) {
  if (!ready.value || loadingAudio.value || audioUrls.value[language]) return
  const generation = audioGeneration, jobId = job.value.id
  loadingAudio.value = language; error.value = ''
  try {
    const blob = await anifApi.loadAnifPreview(jobId, language)
    if (disposed || generation !== audioGeneration || jobId !== job.value.id) return
    audioUrls.value = { ...audioUrls.value, [language]: URL.createObjectURL(blob) }
  } catch (cause) { if (!disposed && generation === audioGeneration) error.value = cause.message || '试听音频未能读取，请重试。' }
  finally { if (generation === audioGeneration) loadingAudio.value = '' }
}

async function activate() {
  if (!canActivate.value || running.value) return
  await act(async () => {
    const result = await anifApi.activateAnifSetup()
    if (disposed) return
    notice.value = result.message || '已选择 Anif 和这个音色，可返回对话播放语音。'
    await refresh(true); emit('changed')
  })
}

async function prepareRemoval() {
  await act(async () => { const result = await anifApi.previewAnifUninstall(); if (!disposed) removal.value = result })
}

async function remove() {
  if (!removal.value?.token) return
  const token = removal.value.token
  await act(async () => {
    const result = await anifApi.uninstallAnifSetup(token)
    if (disposed) return
    removal.value = null; clearAudio()
    if (result.ok === false) error.value = result.message || 'Anif 已停用，但部分文件仍被占用，请稍后重试卸载。'
    else notice.value = result.message || '受管的 Anif 安装已卸载。'
    await refresh(true); emit('changed')
  })
}

onBeforeUnmount(() => { disposed = true; clearTimeout(timer); clearAudio() })
</script>

<template>
  <section class="anif-setup" aria-label="Anif 安装与音色转换">
    <div class="setup-heading"><button type="button" :aria-expanded="opened" @click="toggle">{{ opened ? '收起安装与音色转换' : '安装 Anif / 转换自己的音色' }}</button><span v-if="running" role="status">后台处理中：{{ job.message || '请稍候' }}</span></div>
    <div v-show="opened" class="setup-content">
      <p>选择已导入的 GPT-SoVITS V2 音色，Mio 会准备环境、在本机编译，并检查中文和日语能否发声。完成后先试听，再决定使用。</p>
      <p>这是可选功能。现在可以跳过，之后到“模型与功能”继续。</p>
      <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
      <button type="button" :disabled="loading || busy" @click="refresh()">{{ loading ? '正在检查…' : '重新检查' }}</button>
      <template v-if="snapshot">
        <ul v-if="snapshot.checks?.length" class="checks" aria-label="电脑与安装检查"><li v-for="check in snapshot.checks" :key="check.id"><strong>{{ check.label }}</strong>：{{ check.message || (check.ok ? '可以继续' : '需要处理') }}</li></ul>
        <p v-for="(blocker, index) in snapshot.blockers || []" :key="index" class="error">{{ typeof blocker === 'string' ? blocker : blocker.message }}</p>
        <label class="voice-picker">要使用的音色<select v-model="voiceId" :disabled="busy || running"><option value="" disabled>请选择已导入的 V2 音色</option><option v-for="voice in voices" :key="voice.id" :value="voice.id" :disabled="voice.compatible === false">{{ voice.name || voice.id }}{{ voice.compatible === false ? '（暂不支持）' : '' }}</option></select></label>
        <p v-if="selectedVoice?.reason">{{ selectedVoice.reason }}</p>
        <p v-if="!voices.some(voice => voice.compatible !== false)">还没有可转换的 V2 音色。请先到声音设置导入训练好的两份权重和参考音频。</p>
        <button type="button" :disabled="busy || running" @click="emit('navigate', 'settings-voice')">去声音设置导入音色</button>
        <ol class="steps" aria-label="安装步骤"><li v-for="step in steps" :key="step.id">{{ step.label }}</li></ol>
        <div v-if="job.id" class="job-state" role="status" aria-live="polite"><progress :value="progress" max="100" :aria-label="job.message || '安装进度'" /><p>{{ job.message || '等待处理' }}</p><p v-if="downloadProgress">{{ downloadProgress }}</p><p v-if="job.error || failed" class="error">{{ job.error || '安装未完成，可检查空间与网络后重试。' }}</p><p v-if="running">下载和本机编译可能需要较长时间。收起页面后仍会继续；需要停止时请选择取消。</p></div>
        <div class="actions"><button type="button" :disabled="busy || loading || !canStart" @click="start">{{ failed || job.stage === 'cancelled' ? '重试准备此音色' : snapshot.installed ? '为所选音色重新准备' : '开始安装并编译' }}</button><button v-if="running && job.can_cancel" type="button" :disabled="busy" @click="cancel">取消准备</button></div>
        <div v-if="ready" class="previews">
          <p>“{{ preparedVoiceName }}”的中文和日语发声检查已完成。请听音色与断句是否满意；实际效果和速度也取决于电脑和文本。</p>
          <p v-if="job.voice_id && voiceId !== job.voice_id">当前试听和“使用这个音色”对应“{{ preparedVoiceName }}”。要使用新选择的音色，请先为它编译。</p>
          <div v-for="language in [{ id: 'zh', label: '中文' }, { id: 'ja', label: '日语' }]" :key="language.id" class="preview"><button v-if="!audioUrls[language.id]" type="button" :disabled="Boolean(loadingAudio)" @click="listen(language.id)">{{ loadingAudio === language.id ? '正在读取…' : `读取${language.label}试听` }}</button><template v-else><span>{{ language.label }}试听</span><audio :src="audioUrls[language.id]" controls preload="none" :aria-label="`${language.label}试听`" /></template></div>
        </div>
        <div class="actions"><button v-if="canActivate" type="button" :disabled="busy || running || active" @click="activate">{{ active ? '正在使用这个音色' : '使用这个音色' }}</button><button v-if="canUninstall" type="button" :disabled="busy || running" @click="prepareRemoval">卸载 Anif，释放磁盘</button></div>
        <div v-if="removal" class="removal" role="region" aria-label="确认卸载 Anif"><p>{{ removal.impact }}</p><p>{{ removal.preserved || '原始音色文件与其他语音引擎保留。' }}</p><p>预计释放 {{ formatModelBytes(removal.size_bytes) }}</p><button type="button" :disabled="busy || !removal.token" @click="remove">确认卸载</button><button type="button" :disabled="busy" @click="removal = null">保留，取消卸载</button></div>
        <details><summary>兼容说明</summary><p>Anif 使用 GPT-SoVITS V2 权重进行流式加速。其他版本的音色需要对应适配，不能直接替换。更换显卡或电脑后需要在目标电脑重新编译。</p><p>发声检查验证基本可用，效果请以试听为准。本功能仍为实验支持。</p></details>
      </template>
      <button type="button" @click="opened = false">暂时跳过，返回模型列表</button>
    </div>
  </section>
</template>

<style scoped>
.anif-setup,.setup-content { display:grid; gap:12px; min-width:0; }
.anif-setup { margin-top:12px; }
.setup-heading,.actions,.preview { display:flex; flex-wrap:wrap; align-items:center; gap:10px; min-width:0; }
.setup-content { margin-top:4px; }
.setup-content p { margin:0; overflow-wrap:anywhere; }
.setup-content button,.setup-content select,.setup-heading button { font:inherit; line-height:1.6; color:inherit; border:1px solid var(--line); border-radius:6px; background:var(--surface); padding:7px 11px; max-width:100%; white-space:normal; }
.setup-content button,.setup-heading button { cursor:pointer; }
.setup-content button:disabled { opacity:.5; cursor:default; }
.voice-picker { display:grid; gap:6px; min-width:0; }
.voice-picker select { width:100%; min-width:0; appearance:none; background-image:none; }
.checks,.steps { padding-left:1.5em; margin:0; }
.checks li,.steps li { margin:5px 0; }
.job-state { display:grid; gap:6px; }
.job-state progress { width:100%; accent-color:var(--accent); }
.error { color:var(--danger); }
.previews { display:grid; gap:12px; }
.preview audio { width:100%; max-width:360px; min-width:0; }
.removal { padding:12px; border:1px solid var(--line); border-radius:6px; display:grid; gap:10px; }
details summary { cursor:pointer; list-style:none; text-decoration:underline; text-underline-offset:3px; }
summary::-webkit-details-marker { display:none; }
</style>
