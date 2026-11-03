<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { loadCallStatus } from '../services/companionApi.js'

const status = ref({ active: false, stage: 'idle', turn: 0, timings: {}, firstAudioMs: null })
const loading = ref(false), error = ref(''), updated = ref('')
let disposed = false, timer, controller
const stages = { idle: '尚未通话', connecting: '正在连接', listening: '正在聆听', asr: '正在识别', model: '正在生成回答', committing: '正在完成回答', awaiting_voice: '等待语音', speaking: '正在说话', ended: '通话已结束', error: '通话出现错误' }
const stageLabel = computed(() => stages[status.value.stage] || '状态未确认')
const validMs = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
const rows = computed(() => [
  { key: 'asr_ms', label: '语音识别', ms: validMs(status.value.timings.asr_ms) },
  { key: 'model_first_token_ms', label: '模型首字', ms: validMs(status.value.timings.model_first_token_ms) },
  { key: 'model_ms', label: '模型回答完成', ms: validMs(status.value.timings.model_ms) },
  { key: 'server_total_ms', label: '服务端处理总耗时', ms: validMs(status.value.timings.server_total_ms) },
  { key: 'first_audio_ms', label: '首段音频下发', ms: validMs(status.value.firstAudioMs) },
])
const maxMs = computed(() => Math.max(1, ...rows.value.map(row => row.ms ?? 0)))
function formatMs(value) { return value == null ? '尚未测得' : value < 1000 ? `${Math.round(value)} 毫秒` : `${(value / 1000).toFixed(2)} 秒` }
async function refresh() {
  if (disposed || loading.value) return
  loading.value = true; error.value = ''; controller = new AbortController()
  try {
    const data = await loadCallStatus({ signal: controller.signal })
    if (disposed) return
    // Keep only aggregate measurements. Do not retain transcript, reply or recording metadata.
    const timings = {}
    for (const key of ['asr_ms', 'model_first_token_ms', 'model_ms', 'server_total_ms']) timings[key] = validMs(data?.timings?.[key])
    status.value = { active: data?.active === true, stage: Object.hasOwn(stages, data?.stage) ? data.stage : 'unknown', turn: Number.isSafeInteger(data?.current_turn_id) && data.current_turn_id > 0 ? data.current_turn_id : 0, timings, firstAudioMs: validMs(data?.voice_started?.first_audio_latency_ms) }
    updated.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  } catch (cause) {
    if (!disposed && cause?.code !== 'request_cancelled') error.value = '暂时无法读取通话状态，请稍后刷新。'
  } finally { if (!disposed) loading.value = false }
}
onMounted(() => { void refresh(); timer = setInterval(() => { void refresh() }, 2000) })
onBeforeUnmount(() => { disposed = true; clearInterval(timer); controller?.abort() })
</script>

<template>
  <section class="call-diagnostics-panel" aria-label="通话延迟诊断">
    <div class="call-diagnostics-heading"><span>已测耗时</span><button type="button" :disabled="loading" @click="refresh">{{ loading ? '读取中…' : '刷新状态' }}</button></div>
    <p class="settings-note" role="status">{{ stageLabel }}<template v-if="status.turn">，第 {{ status.turn }} 轮</template><span v-if="updated" class="call-updated">更新于 {{ updated }}</span></p>
    <p v-if="error" class="settings-note" role="alert">{{ error }}</p>
    <ul class="call-timing-list">
      <li v-for="row in rows" :key="row.key"><div><span>{{ row.label }}</span><strong>{{ formatMs(row.ms) }}</strong></div><div class="call-timing-track" aria-hidden="true"><i v-if="row.ms != null" :style="{ width: `${Math.max(1, row.ms / maxMs * 100)}%` }" /></div></li>
    </ul>
    <p class="settings-note">首音频下发不等于扬声器出声。各阶段可能并行，耗时不能直接相加。</p>
    
  </section>
</template>

<style scoped>
.call-diagnostics-panel { font-family: "Microsoft YaHei UI", "Microsoft YaHei", system-ui, sans-serif; font-size: var(--text-base); display: grid; gap: 12px; padding: 14px 0; min-width: 0; }
.call-diagnostics-heading { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
.call-diagnostics-panel p { margin: 0; padding: 0; background: transparent; }
.call-diagnostics-panel .settings-note { font-size: var(--text-xs); color: var(--text-muted); line-height: 1.6; }
.call-diagnostics-heading { font-size: var(--text-base); }
.call-diagnostics-heading button { font: inherit; box-shadow: none; }
.call-updated { display: block; }
.call-timing-list { list-style: none; padding: 0; margin: 0; display: grid; gap: 14px; }
.call-timing-list li > div:first-child { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; margin-bottom: 5px; }
.call-timing-list strong { font-variant-numeric: tabular-nums; }
.call-timing-track { height: 6px; border-radius: 6px; background: var(--surface-soft); overflow: hidden; }
.call-timing-track i { display: block; height: 100%; background: var(--accent); border-radius: inherit; }
</style>
