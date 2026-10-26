<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { Mic, MicOff, PhoneCall, PhoneOff } from '../icons/index.js'
import { createRealtimeVoiceSession } from '../realtimeVoiceSession.js'
import { focusModal, restoreModalFocus, trapModalFocus } from '../modalFocus.js'

const props = defineProps({
  visible: { type: Boolean, default: false },
  instructions: { type: String, default: '' },
})
const emit = defineEmits(['close'])
const state = ref('idle'), error = ref(''), userText = ref(''), replyText = ref(''), muted = ref(false)
let confirmedTranscript = ''
const dialogRef = ref(null)
let focusBeforeOpen = null
const session = createRealtimeVoiceSession({
  instructions: () => props.instructions,
  onState(value) { state.value = value; if (value === 'idle') muted.value = false },
  onError(value) { error.value = value },
  onTranscript(text, final) {
    userText.value = [confirmedTranscript, text].filter(Boolean).join('\n')
    if (final) confirmedTranscript = userText.value
  },
  onReply(text) { replyText.value = text },
})
function connect() {
  confirmedTranscript = ''; userText.value = ''; replyText.value = ''
  session.connect()
}
function close() { session.stop(); emit('close') }
function toggleMute() { muted.value = session.toggleMute() }
const statusLabel = computed(() => ({
  idle: '未开始', connecting: '正在连接…', talking: '正在对话，直接说话即可', error: '连接出现问题',
}[state.value] || ''))
watch(() => props.visible, async visible => {
  if (visible) {
    focusBeforeOpen = document.activeElement
    await nextTick()
    if (props.visible) focusModal(dialogRef.value)
  } else {
    session.stop()
    const target = focusBeforeOpen; focusBeforeOpen = null
    await nextTick(); restoreModalFocus(target)
  }
}, { immediate: true })
function onDialogKeydown(event) {
  if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); return }
  trapModalFocus(event, dialogRef.value)
}
onBeforeUnmount(() => { session.stop(); restoreModalFocus(focusBeforeOpen) })
</script>

<template>
  <div v-if="visible" class="realtime-voice-modal">
    <div ref="dialogRef" class="realtime-voice-card" role="dialog" aria-modal="true" aria-label="实时语音对话" tabindex="-1" @keydown="onDialogKeydown">
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
