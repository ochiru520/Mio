<script setup>
import { computed } from 'vue'
const props = defineProps({ review: { type: Object, required: true } })
const emit = defineEmits(['open-diary'])
const status = computed(() => props.review.source_status || 'unknown')
const label = computed(() => ({ current: '生成时的来源仍有效', stale: '来源已变化，回顾需要更新', unknown: '旧回顾未记录来源版本' }[status.value] || '来源版本待核对'))
const dates = computed(() => props.review.source_dates || props.review.source_diaries?.map(item => item.date) || [])
</script>
<template><section class="review-sources" :class="status" aria-label="回顾来源版本"><strong>{{ label }}</strong><p v-if="status !== 'current'">{{ review.stale_reason || '无法确认这份回顾是否反映最新记录。' }} 重新生成前会保留现有正文。</p><small v-if="review.source_generated_at">生成时间：{{ review.source_generated_at }}</small><details v-if="dates.length"><summary>查看来源日期（{{ dates.length }}）</summary><div><button v-for="date in dates" :key="date" type="button" @click="emit('open-diary', date)">{{ date }}</button></div></details></section></template>
<style scoped>.review-sources{padding:12px 14px;margin:12px 0;border:1px solid var(--line);border-radius:8px;font-size:13px;background:var(--surface)}.review-sources.stale,.review-sources.unknown{border-color:var(--warning)}.review-sources strong{font-weight:600}.review-sources p{margin:6px 0;color:var(--muted)}.review-sources small{display:block;color:var(--muted)}.review-sources details{margin-top:6px}.review-sources summary{cursor:pointer}.review-sources details div{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.review-sources button{font:inherit;color:var(--accent);background:transparent;border:1px solid var(--line);border-radius:5px;padding:5px 8px;cursor:pointer}</style>
