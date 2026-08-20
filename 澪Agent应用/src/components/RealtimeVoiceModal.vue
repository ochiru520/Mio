<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { Mic, MicOff, PhoneCall, PhoneOff } from '@lucide/vue'

const props = defineProps({
  visible: { type: Boolean, default: false },
  instructions: { type: String, default: '' },
})
const emit = defineEmits(['close'])

const state = ref('idle') // idle | connecting | talking | error
const error = ref('')
const userText = ref('')
const replyText = ref('')
const muted = ref(false)

let socket = null
let audioContext = null
let mediaStream = null
let processor = null
let inputAnalyser = null
let activeSource = null
let currentSource = null
const transcriptBuffer = []

const TARGET_RATE = 16000

function connect() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  socket = new WebSocket(`${protocol}//${window.location.host}/api/realtime/voice`)
  socket.onopen = () => {
    state.value = 'connecting'
    socket.send(JSON.stringify({ type: 'start', instructions: props.instructions }))
  }
  socket.onmessage = async (event) => {
    let message
    try { message = JSON.parse(event.data) } catch (_) { return }
    if (message.type === 'started') {
      state.value = 'talking'
      await startMicrophone()
    } else if (message.type === 'asr') {
      userText.value = message.final
        ? (transcriptBuffer.join('') || '')
        : (transcriptBuffer.join('') || '') + '…'
      if (message.final) transcriptBuffer.length = 0
    } else if (message.type === 'chat') {
      replyText.value = message.text
    } else if (message.type === 'tts_audio') {
      playPcm(message.data)
    } else if (message.type === 'error') {
      error.value = message.message
      state.value = 'error'
    } else if (message.type === 'stopped') {
      state.value = 'idle'
    }
  }
  socket.onclose = () => {
    state.value = 'idle'
    stopMicrophone()
  }
  socket.onerror = () => {
    error.value = '实时语音连接失败，请检查云端语音配置。'
    state.value = 'error'
  }
}

function close() {
  if (socket) {
    try { socket.send(JSON.stringify({ type: 'stop' })) } catch (_) {}
    try { socket.close() } catch (_) {}
    socket = null
  }
  stopMicrophone()
  state.value = 'idle'
  emit('close')
}

async function startMicrophone() {
  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 48000 })
  }
  if (audioContext.state === 'suspended') await audioContext.resume()
  if (!mediaStream) {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } })
  }
  const source = audioContext.createMediaStreamSource(mediaStream)
  processor = audioContext.createScriptProcessor(4096, 1, 1)
  inputAnalyser = audioContext.createAnalyser()
  inputAnalyser.fftSize = 2048
  source.connect(processor)
  processor.connect(audioContext.destination)
  processor.onaudioprocess = (event) => {
    if (!socket || socket.readyState !== WebSocket.OPEN) return
    const input = event.inputBuffer.getChannelData(0)
    const downsampled = downsample(input, audioContext.sampleRate, TARGET_RATE)
    const pcm = floatTo16BitPcm(downsampled)
    const base64 = arrayBufferToBase64(pcm.buffer)
    socket.send(JSON.stringify({ type: 'audio', data: base64 }))
    transcriptBuffer.push('')
  }
}

function stopMicrophone() {
  if (processor) {
    try { processor.disconnect() } catch (_) {}
    processor = null
  }
  if (mediaStream) {
    for (const track of mediaStream.getTracks()) track.stop()
    mediaStream = null
  }
  if (audioContext && audioContext.state !== 'closed') {
    // 保留 AudioContext 供播放使用，不关闭
  }
}

function toggleMute() {
  muted.value = !muted.value
  if (mediaStream) {
    for (const track of mediaStream.getTracks()) track.enabled = !muted.value
  }
}

function downsample(input, fromRate, toRate) {
  const ratio = fromRate / toRate
  const length = Math.floor(input.length / ratio)
  const output = new Float32Array(length)
  for (let i = 0; i < length; i += 1) {
    const start = Math.floor(i * ratio)
    const end = Math.min(input.length, Math.floor((i + 1) * ratio))
    let sum = 0
    for (let j = start; j < end; j += 1) sum += input[j]
    output[i] = end > start ? sum / (end - start) : 0
  }
  return output
}

function floatTo16BitPcm(samples) {
  const buffer = new ArrayBuffer(samples.length * 2)
  const view = new DataView(buffer)
  for (let i = 0; i < samples.length; i += 1) {
    const value = Math.max(-1, Math.min(1, samples[i]))
    view.setInt16(i * 2, value < 0 ? value * 0x8000 : value * 0x7fff, true)
  }
  return new Uint8Array(buffer)
}

function arrayBufferToBase64(buffer) {
  let binary = ''
  const bytes = new Uint8Array(buffer)
  const chunk = 0x8000
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk))
  }
  return btoa(binary)
}

async function playPcm(base64) {
  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 48000 })
  }
  if (audioContext.state === 'suspended') await audioContext.resume()
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
  const view = new DataView(bytes.buffer)
  const samples = new Float32Array(bytes.length / 2)
  for (let i = 0; i < samples.length; i += 1) {
    samples[i] = view.getInt16(i * 2, true) / 0x8000
  }
  const upsampled = upsample(samples, 24000, audioContext.sampleRate)
  const buffer = audioContext.createBuffer(1, upsampled.length, audioContext.sampleRate)
  buffer.copyToChannel(upsampled, 0)
  stopCurrentSource()
  const source = audioContext.createBufferSource()
  source.buffer = buffer
  source.connect(audioContext.destination)
  source.onended = () => {
    if (currentSource === source) currentSource = null
  }
  currentSource = source
  source.start()
}

function stopCurrentSource() {
  if (currentSource) {
    try { currentSource.stop() } catch (_) {}
    currentSource = null
  }
}

function upsample(input, fromRate, toRate) {
  const ratio = toRate / fromRate
  const length = Math.round(input.length * ratio)
  const output = new Float32Array(length)
  for (let i = 0; i < length; i += 1) {
    const position = i / ratio
    const index = Math.floor(position)
    const fraction = position - index
    const a = input[index] || 0
    const b = input[index + 1] || a
    output[i] = a + (b - a) * fraction
  }
  return output
}

const statusLabel = computed(() => ({
  idle: '未开始',
  connecting: '正在连接…',
  talking: '正在对话，直接说话即可',
  error: '连接出现问题',
}[state.value] || ''))

onBeforeUnmount(() => {
  close()
})
</script>

<template>
  <div v-if="visible" class="realtime-voice-modal">
    <div class="realtime-voice-card">
      <header>
        <div><PhoneCall :size="18" /><strong>实时语音对话</strong></div>
        <button type="button" aria-label="关闭" @click="close">×</button>
      </header>
      <div class="realtime-voice-body">
        <p class="realtime-voice-intro">像打电话一样直接说，Mio 会边听边答，随时可以打断。需要已配置云端语音 Key。</p>
        <div class="realtime-voice-status" :class="state">
          <i /><span>{{ statusLabel }}</span>
        </div>
        <div v-if="error" class="realtime-voice-error">{{ error }}</div>
        <div class="realtime-voice-transcript">
          <div v-if="userText" class="realtime-user-text"><small>你</small><span>{{ userText }}</span></div>
          <div v-if="replyText" class="realtime-reply-text"><small>Mio</small><span>{{ replyText }}</span></div>
          <div v-if="!userText && !replyText" class="realtime-voice-placeholder">对话内容会显示在这里</div>
        </div>
        <div class="realtime-voice-actions">
          <button v-if="state !== 'talking' && state !== 'connecting'" class="primary" type="button" @click="connect"><Mic :size="15" />开始对话</button>
          <button v-else type="button" :class="{ active: muted }" @click="toggleMute"><component :is="muted ? MicOff : Mic" :size="15" />{{ muted ? '已静音' : '麦克风开' }}</button>
          <button class="danger" type="button" @click="close"><PhoneOff :size="15" />结束</button>
        </div>
      </div>
    </div>
  </div>
</template>
