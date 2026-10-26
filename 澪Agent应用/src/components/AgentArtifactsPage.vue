<script setup>
import * as artifactsApi from '../services/artifactsApi.js'

import { mergeLoadedItems } from '../scrollCollections.js'
import WorkspacePager from './WorkspacePager.vue'
import { onBeforeUnmount, ref, watch } from 'vue'
import { Search, Star, Grid2X2, List, FileText, Image, Film, Music, Archive, RefreshCw } from '../icons/index.js'
const props = defineProps({ projects: { type: Array, default: () => [] } })
const emit = defineEmits(['preview', 'open-source', 'tools'])
const kind = ref(''), projectId = ref(''), query = ref(''), favorite = ref(false), grid = ref(false)
const items = ref([]), offset = ref(0), hasMore = ref(false), loading = ref(false), error = ref('')
const actionBusy = ref(false)
const kinds = [{ id: '', label: '全部成果', icon: Archive }, { id: 'document', label: '文档', icon: FileText }, { id: 'image', label: '图像', icon: Image }, { id: 'video', label: '视频', icon: Film }, { id: 'audio', label: '音频', icon: Music }]
let version = 0, timer, disposed = false
async function load(reset = false) {
  if (reset) { offset.value=0; items.value=[]; hasMore.value=false }
  const n = ++version
  loading.value = true
  const params = new URLSearchParams({ project_id: projectId.value, q: query.value, kind: kind.value, favorite: String(favorite.value), offset: String(offset.value), limit: '40' })
  try { const data = await artifactsApi.loadArtifacts(params, {}); if (disposed || n !== version) return; items.value = mergeLoadedItems(items.value,data.artifacts,offset.value); hasMore.value = data.has_more; error.value = '' }
  catch (cause) { if (!disposed && n === version) { items.value = []; hasMore.value = false; error.value = cause.message } }
  finally { if (n === version) loading.value = false }
}
async function toggleFavorite(item) {
  await mutate(item, 'favorite', { favorite: !item.favorite })
}
async function mutate(item, operation, body) {
  if (actionBusy.value || loading.value) return
  actionBusy.value = true
  try {
    await artifactsApi.mutateArtifact(item.id, operation, body)
    if (!disposed) await load()
  } catch (cause) { if (!disposed) error.value = cause.message }
  finally { actionBusy.value = false }
}
watch([kind, projectId, favorite], () => load(true), { immediate: true })
watch(query, () => { clearTimeout(timer); timer = window.setTimeout(() => load(true), 200) })
onBeforeUnmount(() => { disposed = true; ++version; clearTimeout(timer) })
</script>
<template>
  <section class="agent-library-layout">
    <aside class="agent-content-sidebar"><h2>项目空间</h2><nav aria-label="成果分类"><button v-for="item in kinds" :key="item.id" type="button" :class="{ selected: kind === item.id && !favorite }" @click="kind = item.id; favorite = false"><component :is="item.icon" :size="16" />{{ item.label }}</button><button type="button" :class="{ selected: favorite }" @click="favorite = !favorite"><Star :size="16" />收藏</button></nav><small>按项目</small><button type="button" :class="{ selected: !projectId }" @click="projectId = ''">所有项目</button><button v-for="project in projects" :key="project.id" type="button" :class="{ selected: projectId === project.id }" @click="projectId = project.id">{{ project.name }}</button><button type="button" class="agent-sidebar-bottom" @click="emit('tools')">项目工具</button></aside>
    <main class="agent-library-main"><header><h1>{{ favorite ? '收藏的成果' : kinds.find(item => item.id === kind).label }}</h1><div class="agent-inline-actions"><label class="agent-search"><Search :size="15" /><input v-model="query" type="search" aria-label="搜索成果" placeholder="搜索成果" /></label><button type="button" aria-label="刷新成果" :disabled="loading" @click="load"><RefreshCw :size="16" /></button><button type="button" :aria-label="grid ? '切换列表' : '切换网格'" @click="grid = !grid"><component :is="grid ? List : Grid2X2" :size="16" /></button></div></header>
      <p v-if="error" role="alert">{{ error }}</p><p v-else-if="loading" class="agent-muted">正在读取成果…</p><p v-else-if="!items.length" class="agent-empty-state">{{ query ? '没有匹配的成果' : '这里还没有生成的成果' }}</p>
      <div v-if="!loading && !error" :class="['agent-artifact-collection', { grid }]"><article v-for="item in items" :key="item.id" :data-artifact-id="item.id"><button type="button" class="artifact-open" @click="emit('preview', item)"><img v-if="grid && item.integrity_status !== 'failed' && item.mime_type.startsWith('image/')" :src="item.url" :alt="item.name" loading="lazy" /><FileText v-else :size="18" /><span><strong>{{ item.name }}</strong><small>v{{ item.version }} · {{ item.created_at?.slice(0,16).replace('T',' ') }} · {{ Math.max(1,Math.round(item.size/1024)) }} KB<template v-if="item.integrity_status === 'failed'"> · 文件校验失败</template></small></span></button><div class="agent-inline-actions"><button type="button" :aria-label="`${item.favorite ? '取消收藏' : '收藏'}${item.name}`" :aria-pressed="item.favorite" :disabled="actionBusy" @click="toggleFavorite(item)"><Star :size="15" /></button><button v-if="item.conversation_id || item.task_id" type="button" @click="emit('open-source', item)">来源对话</button><a v-if="item.integrity_status !== 'failed'" :href="item.url" :download="item.name">下载</a><button v-else type="button" :disabled="actionBusy" @click="mutate(item, 'verify')">重新校验</button></div></article></div>
      <WorkspacePager :has-more="hasMore && !error" :busy="loading || actionBusy" @page="offset += 40; load()" />
    </main>
  </section>
</template>
