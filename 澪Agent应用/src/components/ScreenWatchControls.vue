<script setup>
import { computed } from 'vue'
import SelectField from './SelectField.vue'

const props = defineProps({
  settings: { type: Object, required: true },
  watch: { type: Object, default: () => ({}) },
  running: Boolean,
})
const mode = computed({ get: () => props.settings.screen_watch_mode || 'single', set: value => { props.settings.screen_watch_mode = value } })
const pace = computed({ get: () => props.settings.screen_watch_pace || 'normal', set: value => { props.settings.screen_watch_pace = value } })
</script>

<template>
  <div class="screen-watch-controls" aria-label="桌宠陪看设置">
    <label class="screen-watch-field"><span><strong>观察方式</strong><small>动态陪看持续筛选变化画面，有积累时合并最近的过程</small></span><SelectField v-model="mode" aria-label="观察方式"><option value="single">单张观察</option><option value="adaptive">动态陪看（试用）</option></SelectField></label>
    <label v-if="mode === 'adaptive'" class="screen-watch-field"><span><strong>变化灵敏度</strong><small>画面有有效变化就提交，不设固定分析间隔；识别和回答仍需时间</small></span><SelectField v-model="pace" aria-label="变化灵敏度"><option value="calm">安静 · 仅明显变化</option><option value="normal">正常 · 常规变化</option><option value="dense">灵敏 · 更小变化</option></SelectField></label>
    <label class="screen-watch-field"><span><strong>同时听系统声音</strong><small>可选，仅在你开始观察后采集；关闭后只看画面</small></span><span class="switch-control"><input v-model="settings.screen_audio_enabled" type="checkbox" aria-label="同时听系统声音" /><i /></span></label>
    <p class="screen-watch-note">应用修改只保存设置。点击“开始观察”才开始采集，点击“停止”结束观察。陪看回应显示在桌宠气泡中；是否朗读沿用语音设置。</p>
    <p v-if="running && watch.mode === 'adaptive'" class="screen-watch-note" role="status">动态陪看中 · 已选 {{ watch.selected_frames || 0 }} 帧 · 跳过 {{ watch.skipped_frames || 0 }} 帧 · 待合并 {{ watch.buffered_frames || 0 }} 帧 · 已送出 {{ watch.sheets || 0 }} 组</p>
  </div>
</template>

<style scoped>
.screen-watch-controls { min-width: 0; }
.screen-watch-field { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 17px 0; border-bottom: 1px solid var(--border); }
.screen-watch-field > span:first-child { display: grid; gap: 5px; min-width: 0; }
.screen-watch-field strong { color: var(--text); font-size: var(--text-base); font-weight: 600; }
.screen-watch-field small, .screen-watch-note { color: var(--text-muted); font-size: var(--text-xs); line-height: 1.7; overflow-wrap: anywhere; }
.screen-watch-field :deep(.select-field) { flex: 0 1 230px; min-width: 170px; }
.screen-watch-note { margin: 12px 0; }
@media (max-width: 600px) { .screen-watch-field { flex-wrap: wrap; gap: 10px; } .screen-watch-field :deep(.select-field) { flex: 1 1 100%; } }
</style>
