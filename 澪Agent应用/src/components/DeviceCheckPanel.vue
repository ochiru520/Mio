<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { loadAudioDevices, saveAudioOutput } from '../services/modelManagementApi.js'

const keys = { inputDeviceId: 'mio.audio.input', outputDeviceId: 'mio.audio.output', mode: 'mio.audio.mode' }
function stored(key, fallback = '') { try { return localStorage.getItem(key) || fallback } catch { return fallback } }
const inputDeviceId = ref(stored(keys.inputDeviceId)), outputDeviceId = ref(stored(keys.outputDeviceId)), mode = ref(stored(keys.mode, 'headphones'))
const inputs = ref([]), outputs = ref([]), message = ref('尚未测试。点“检查设备”才会申请麦克风权限。'), error = ref('')
const nativeOutputs = ref([]), nativeOutput = ref('default'), nativeAvailable = ref(false), nativeError = ref(''), nativeBusy = ref(false)
const recording = ref(false), requesting = ref(false), audioUrl = ref(''), player = ref(null), seconds = ref(0)
const supportsOutput = typeof HTMLMediaElement !== 'undefined' && typeof HTMLMediaElement.prototype.setSinkId === 'function'
let stream, recorder, stopTimer, tickTimer, disposed = false, chunks = [], interrupted = false

async function enumerate() {
  const devices = await navigator.mediaDevices.enumerateDevices()
  if (disposed) return
  inputs.value = devices.filter(device => device.kind === 'audioinput')
  outputs.value = devices.filter(device => device.kind === 'audiooutput')
  if (inputDeviceId.value && !inputs.value.some(device => device.deviceId === inputDeviceId.value)) { if (recording.value) { interrupted = true; stopRecording() }; inputDeviceId.value = ''; message.value = '原麦克风已断开，已切回系统默认，请重新测试。'; save(false) }
  if (outputDeviceId.value && !outputs.value.some(device => device.deviceId === outputDeviceId.value)) { outputDeviceId.value = ''; message.value = '原扬声器已断开，已切回系统默认，请重新测试。'; save(false) }
}

async function checkDevices() {
  if (requesting.value || recording.value) return
  requesting.value = true; error.value = ''
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('当前窗口不支持麦克风，请使用 Mio 桌面程序并检查 Windows 麦克风权限。')
    const permissionStream = await navigator.mediaDevices.getUserMedia({ audio: true })
    permissionStream.getTracks().forEach(track => track.stop())
    if (disposed) return
    await enumerate()
    message.value = inputs.value.length ? '已找到麦克风。接下来录一段话，回放检查声音是否清晰。' : '没有找到麦克风，请连接后重试。'
  } catch (cause) { error.value = cause.name === 'NotAllowedError' ? '麦克风权限被拒绝，请在 Windows 和当前窗口的权限设置中允许后重试。' : cause.message }
  finally { requesting.value = false }
}

function stopTracks() { stream?.getTracks().forEach(track => track.stop()); stream = null; clearTimeout(stopTimer); clearInterval(tickTimer) }
function stopRecording() {
  if (recorder?.state === 'recording') recorder.stop()
  recording.value = false; stopTracks()
}

async function record() {
  if (recording.value || requesting.value) return
  requesting.value = true; error.value = ''
  try {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') throw new Error('此窗口暂不支持录音测试，请使用 Mio 桌面程序。')
    stream = await navigator.mediaDevices.getUserMedia({ audio: { ...(inputDeviceId.value ? { deviceId: { exact: inputDeviceId.value } } : {}), echoCancellation: mode.value === 'speakers', noiseSuppression: true } })
    if (disposed) { stopTracks(); return }
    if (audioUrl.value) URL.revokeObjectURL(audioUrl.value)
    audioUrl.value = ''; chunks = []; interrupted = false
    stream.getAudioTracks().forEach(track => { track.onended = () => { interrupted = true; stopRecording(); error.value = '麦克风已断开，录音中止，请连接设备后重新测试。' } })
    recorder = new MediaRecorder(stream)
    recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data) }
    recorder.onstop = async () => {
      if (disposed) return
      audioUrl.value = URL.createObjectURL(new Blob(chunks, { type: recorder.mimeType || 'audio/webm' }))
      message.value = interrupted ? '录音被设备变化中断，请重新检查麦克风。' : '录音已完成，请点击下方播放试听。录音只保存在当前窗口，关闭后清除。'
      await nextTick(); await applyOutput()
    }
    recorder.start(); recording.value = true; seconds.value = 0
    tickTimer = setInterval(() => seconds.value += 1, 1000)
    stopTimer = setTimeout(stopRecording, 5000)
    message.value = '正在录音，请说一句完整的话…'
  } catch (cause) { stopTracks(); error.value = cause.name === 'NotAllowedError' ? '未获得麦克风权限，请允许后重试。' : cause.message }
  finally { requesting.value = false }
}

async function applyOutput() {
  if (!player.value || !supportsOutput) return
  try { await player.value.setSinkId(outputDeviceId.value) } catch (cause) { error.value = `输出设备切换失败：${cause.message}。请重新选择或使用系统默认。` }
}

function save(show = true) {
  try {
    localStorage.setItem(keys.inputDeviceId, inputDeviceId.value)
    localStorage.setItem(keys.outputDeviceId, outputDeviceId.value)
    localStorage.setItem(keys.mode, mode.value)
    window.dispatchEvent(new CustomEvent('mio-audio-devices-changed', { detail: { inputDeviceId: inputDeviceId.value, outputDeviceId: outputDeviceId.value, mode: mode.value } }))
    if (show) message.value = '麦克风与回放偏好已保存在这台电脑。Mio 语音输出使用上方单独的设置。'
  } catch { error.value = '设备选择暂时无法保存，请检查本地存储是否可用。' }
}

async function devicesChanged() { try { await enumerate() } catch { error.value = '设备列表已变化，请重新检查设备。' } }
async function refreshNative() {
  nativeBusy.value = true; nativeError.value = ''
  try { const result = await loadAudioDevices(); nativeOutputs.value = result.outputs || []; nativeOutput.value = result.output_device_id || 'default'; nativeAvailable.value = result.available === true }
  catch (cause) { nativeAvailable.value = false; nativeError.value = `Mio 输出设备暂不可用：${cause.message}` }
  finally { nativeBusy.value = false }
}
async function saveNative() {
  nativeBusy.value = true; nativeError.value = ''
  try { const result = await saveAudioOutput(nativeOutput.value); nativeOutput.value = result.output_device_id || nativeOutput.value; message.value = '已保存 Mio 语音输出设备，请到声音设置中试听确认。' }
  catch (cause) { nativeError.value = cause.message }
  finally { nativeBusy.value = false }
}
onMounted(() => { navigator.mediaDevices?.addEventListener?.('devicechange', devicesChanged); refreshNative() })
onBeforeUnmount(() => { disposed = true; stopRecording(); if (audioUrl.value) URL.revokeObjectURL(audioUrl.value); navigator.mediaDevices?.removeEventListener?.('devicechange', devicesChanged) })
</script>

<template>
  <section class="device-check" aria-label="麦克风与扬声器测试">
    <h3>试试麦克风与声音</h3><p>录音只在本机回放，不上传。</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p><p role="status">{{ message }}</p>
    <div class="native-output"><label>Mio 语音输出（正式说话）<select v-model="nativeOutput" :disabled="nativeBusy || !nativeAvailable"><option value="default">Windows 系统默认输出</option><option v-for="device in nativeOutputs.filter(device => device.id !== 'default')" :key="device.id" :value="device.id">{{ device.label }}</option></select></label><p v-if="nativeError" role="alert" class="error">{{ nativeError }}</p><button type="button" :disabled="nativeBusy || !nativeAvailable" @click="saveNative">保存 Mio 输出设备</button><button type="button" :disabled="nativeBusy" @click="refreshNative">刷新输出设备</button></div>
    <button type="button" :disabled="recording || requesting" @click="checkDevices">{{ requesting ? '正在请求设备…' : '检查设备' }}</button>
    <label>麦克风<select v-model="inputDeviceId" :disabled="recording || requesting"><option value="">系统默认麦克风</option><option v-for="(device, index) in inputs" :key="device.deviceId" :value="device.deviceId">{{ device.label || `麦克风 ${index + 1}` }}</option></select></label>
    <details class="device-advanced"><summary>回放设备与回声设置</summary>
    <label>录音回放设备（只影响下方试听）<select v-model="outputDeviceId" :disabled="!supportsOutput || recording" @change="applyOutput"><option value="">系统默认输出</option><option v-for="(device, index) in outputs" :key="device.deviceId" :value="device.deviceId">{{ device.label || `输出设备 ${index + 1}` }}</option></select></label>
    <small v-if="!supportsOutput">此窗口不支持单独选择扬声器，将使用 Windows 默认输出设备。</small>
    <label>使用方式<select v-model="mode" :disabled="recording"><option value="headphones">戴耳机（推荐，减少回声）</option><option value="speakers">外放（请求回声消除）</option></select></label><small>回声消除取决于设备与系统；Mio 听见自己的声音时，请先降低音量或戴耳机。</small>
    </details>
    <div class="actions"><button type="button" :disabled="requesting" @click="recording ? stopRecording() : record()">{{ recording ? `停止录音（${seconds} 秒）` : '录音 5 秒' }}</button><button type="button" :disabled="recording || requesting" @click="save()">保存设备选择</button></div>
    <audio v-if="audioUrl" ref="player" :src="audioUrl" controls preload="metadata" />
    <p>识别与合成效果请到模型中心实测。</p>
  </section>
</template>

<style scoped>
.device-check { padding:0; border:0; font-size: var(--text-base); line-height:1.7; min-width:0; max-width:720px; }
.device-check h3 { margin:0 0 6px; font-size: var(--text-lg); }
.device-check p { margin:6px 0 12px; }
.device-check label { display:block; margin:12px 0 5px; }
.device-check select { display:block; width:100%; margin-top:5px; appearance:none; background-image:none; }
.device-check select,.device-check button { font:inherit; color:inherit; border:1px solid var(--line); border-radius:6px; padding:8px 10px; background:var(--surface); max-width:100%; }
/* The settings shell fixes native selects at 34px. Reset that height so its
   content box still fits the inherited font after padding and text scaling. */
.device-check select { box-sizing:border-box; height:auto; min-height:max(40px, calc(1.5em + 18px)); line-height:1.5; }
.device-check button { cursor:pointer; }
.device-check button:disabled { opacity:.5; }
.device-check small { display:block; color:var(--text-muted); font-size: var(--text-xs); }
.device-check .actions { display:flex; gap:8px; flex-wrap:wrap; margin:12px 0; }
.device-check audio { width:100%; max-width:100%; }
.device-check .error { color:var(--danger); }
.native-output { padding-bottom:12px; margin-bottom:12px; border-bottom:1px solid var(--line); }
.native-output button { margin:0 8px 5px 0; }
.device-advanced { margin:16px 0; }
.device-advanced summary { cursor:pointer; list-style:none; text-decoration:underline; text-underline-offset:3px; }
.device-advanced summary::-webkit-details-marker { display:none; }
</style>
