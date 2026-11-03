<script setup>
import { computed } from 'vue'
const props = defineProps({ runtime: { type: Object, default: () => ({}) } })
defineEmits(['refresh', 'environment'])
const configured = computed(() => props.runtime.configured_model || props.runtime.model || '')
const running = computed(() => props.runtime.owned_server === true || props.runtime.server_running === true)
const stopped = computed(() => props.runtime.owned_server === false && props.runtime.server_running !== true)
const known = computed(() => running.value && props.runtime.model_observation_known === true)
const loaded = computed(() => known.value ? (props.runtime.loaded_models || []) : [])
const serviceLabel = computed(() => stopped.value ? '服务未启动' : running.value ? '服务已启动' : '服务状态待确认')
const loadedLabel = computed(() => stopped.value ? '无（服务未启动）' : !known.value ? '当前加载情况待确认' : loaded.value.length ? loaded.value.join('、') : '无（模型尚未加载）')
const mismatch = computed(() => known.value && loaded.value.length && configured.value && !loaded.value.some(name => [configured.value.toLowerCase(), `${configured.value.toLowerCase()}:latest`].includes(name.toLowerCase())))
</script>

<template>
  <section class="local-vision-identity" aria-label="本地视觉模型">
    <div><strong>本地视觉模型</strong><span>{{ serviceLabel }}</span></div>
    <p>配置模型：<b>{{ configured || '尚未读取' }}</b></p>
    <p>已加载模型：{{ loadedLabel }}</p>
    <p v-if="mismatch" role="status">当前加载的模型与配置不同，后续观察请求使用配置模型。</p>
    <p v-if="!known && !stopped">刷新状态后可确认实际加载的模型。</p>
    <div class="local-vision-actions"><button type="button" @click="$emit('refresh')">刷新模型状态</button><button type="button" @click="$emit('environment')">模型管理与安装</button></div>
  </section>
</template>

<style scoped>
.local-vision-identity{padding:14px 16px;min-width:0;max-width:100%;border-bottom:1px solid var(--line);font-size:var(--text-base);line-height:1.6}
.local-vision-identity>div:first-child{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
.local-vision-identity p{margin:6px 0;overflow-wrap:anywhere}
.local-vision-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.local-vision-actions button{font:inherit;color:var(--text);background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:7px 11px;max-width:100%;cursor:pointer}
</style>
