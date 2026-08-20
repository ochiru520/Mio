<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Check, CheckCircle2, CircleAlert, CircleHelp, Download, ExternalLink, RefreshCw, RotateCw, Settings2, Wrench } from '@lucide/vue'
import { installDependency, loadDependencies, loadDependencyStatus } from '../services/dependenciesApi.js'

const props = defineProps({
  compact: { type: Boolean, default: false },
})
const emit = defineEmits(['refresh-environment'])

const busy = ref(false)
const error = ref('')
const dependencies = ref([])
const progress = ref({})
const pollTimer = ref(null)

const statusMeta = {
  ready: { label: '已就绪', icon: CheckCircle2, tone: 'ok' },
  configured: { label: '已配置', icon: CheckCircle2, tone: 'ok' },
  unconfigured: { label: '未配置', icon: CircleHelp, tone: 'hint' },
  missing: { label: '缺失', icon: CircleAlert, tone: 'warn' },
}

async function refresh() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const result = await loadDependencies()
    dependencies.value = result.dependencies || []
    const nextProgress = {}
    for (const item of dependencies.value) {
      if (!['ready', 'configured'].includes(item.status) && item.progress) nextProgress[item.id] = item.progress
    }
    progress.value = nextProgress
    emit('refresh-environment')
  } catch (err) {
    error.value = err.message || '依赖检查失败'
  } finally {
    busy.value = false
  }
}

function activeInstalls() {
  return dependencies.value.filter((item) => (
    !['ready', 'configured'].includes(item.status)
    && (item.installing || progress.value[item.id]?.installing)
  ))
}

function startPolling() {
  if (pollTimer.value) return
  pollTimer.value = setInterval(async () => {
    const targets = activeInstalls()
    if (!targets.length) return
    let completedThisTick = false
    for (const item of targets) {
      try {
        const status = await loadDependencyStatus(item.id)
        progress.value[item.id] = status
        if (!status.installing) {
          completedThisTick = true
          const dep = dependencies.value.find((entry) => entry.id === item.id)
          if (dep) {
            dep.installing = false
            if (status.error) dep.last_error = status.error
          }
        }
      } catch (err) {
        // 单次轮询失败不打断整体
      }
    }
    if (!activeInstalls().length) {
      stopPolling()
      if (completedThisTick) await refresh()
    }
  }, 1500)
}

function stopPolling() {
  if (pollTimer.value) {
    clearInterval(pollTimer.value)
    pollTimer.value = null
  }
}

async function install(item) {
  if (busy.value || item.installing) return
  busy.value = true
  error.value = ''
  try {
    const started = await installDependency(item.id)
    item.installing = true
    item.last_error = ''
    progress.value[item.id] = { installing: true, stage: 'starting', percent: 0, message: started.message || '正在启动安装…' }
    startPolling()
  } catch (err) {
    error.value = err.message || '安装启动失败'
  } finally {
    busy.value = false
  }
}

function openUrl(url) {
  if (url) window.open(url, '_blank', 'noopener')
}

function actionFor(item) {
  if (item.kind === 'builtin') return { kind: 'none' }
  if (item.kind === 'configure') {
    if (item.id === 'cloud_model') return { kind: 'navigate', label: '去配置', target: 'settings-models' }
    if (item.id === 'cloud_tts') return { kind: 'navigate', label: '去配置', target: 'settings-voice' }
    return { kind: 'none' }
  }
  if (['ready', 'configured'].includes(item.status)) {
    if (item.kind === 'manual') return { kind: 'manual', label: item.manual_label || '打开官方下载页', url: item.manual_url }
    return { kind: 'none' }
  }
  if (item.installing) return { kind: 'progress' }
  if (item.kind === 'script') return { kind: 'install', label: '一键安装' }
  if (item.kind === 'manual') return { kind: 'manual', label: item.manual_label || '打开官方下载页', url: item.manual_url }
  return { kind: 'none' }
}

function percentOf(item) {
  const value = progress.value[item.id]?.percent
  return Number.isFinite(Number(value)) ? Math.max(0, Math.min(100, Number(value))) : 0
}

function speedLabel(item) {
  const value = Number(progress.value[item.id]?.speed_mb_s || 0)
  return value > 0 ? `${value.toFixed(1)} MB/s` : ''
}

const summary = computed(() => {
  const ready = dependencies.value.filter((item) => ['ready', 'configured'].includes(item.status)).length
  return { ready, total: dependencies.value.length }
})

onMounted(refresh)
onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="dependency-center" :class="{ compact }">
    <div class="dependency-center-heading">
      <div>
        <strong>环境与模型中心</strong>
        <small>检查这台电脑上有什么、缺什么；缺的可以一键下载，下载窗口不会挡住后面的设置。</small>
      </div>
      <button type="button" :disabled="busy" @click="refresh">
        <RefreshCw :class="{ spin: busy }" :size="14" />重新检查
      </button>
    </div>

    <div v-if="dependencies.length" class="dependency-summary">
      <span><strong>{{ summary.ready }}</strong><small>已就绪 / {{ summary.total }} 项</small></span>
      <span v-for="item in dependencies.filter((entry) => entry.installing)" :key="item.id" class="dependency-summary-installing">
        <RotateCw class="spin" :size="12" /><small>{{ item.label }} {{ percentOf(item) }}%</small>
      </span>
    </div>

    <div v-if="error" class="dependency-error">{{ error }}</div>

    <ul class="dependency-list">
      <li v-for="item in dependencies" :key="item.id" :class="['dependency-item', `status-${item.status}`, { installing: item.installing }]">
        <div class="dependency-item-main">
          <component :is="statusMeta[item.status]?.icon || CircleHelp" :size="17" :class="statusMeta[item.status]?.tone" />
          <div class="dependency-item-text">
            <div class="dependency-item-title">
              <strong>{{ item.label }}</strong>
              <b :class="statusMeta[item.status]?.tone">{{ statusMeta[item.status]?.label || item.status }}</b>
            </div>
            <p>{{ item.what }}</p>
            <small v-if="item.status !== 'ready' && item.status !== 'configured'">{{ item.missing_effect }}</small>
            <small class="dependency-how">怎么装：{{ item.how }}</small>
            <small v-if="item.size_label" class="dependency-size">体积：{{ item.size_label }}</small>
            <small v-if="item.install_path" class="dependency-path">一键安装位置：{{ item.install_path }}</small>
            <div v-if="item.last_error" class="dependency-last-error">上次安装：{{ item.last_error }}</div>
          </div>
          <div class="dependency-item-actions">
            <template v-if="actionFor(item).kind === 'install'">
              <button class="primary" type="button" :disabled="busy" @click="install(item)"><Download :size="14" />{{ actionFor(item).label }}</button>
            </template>
            <template v-else-if="actionFor(item).kind === 'manual'">
              <button type="button" @click="openUrl(actionFor(item).url)"><ExternalLink :size="14" />{{ actionFor(item).label }}</button>
            </template>
            <template v-else-if="actionFor(item).kind === 'navigate'">
              <button type="button" @click="emit('navigate', actionFor(item).target)"><Settings2 :size="14" />{{ actionFor(item).label }}</button>
            </template>
            <template v-else-if="actionFor(item).kind === 'progress'">
              <div class="dependency-progress">
                <i :style="{ width: percentOf(item) + '%' }" />
              </div>
              <small class="dependency-progress-message">{{ progress[item.id]?.message || '安装窗口已打开' }}</small>
              <small v-if="progress[item.id]?.target_path" class="dependency-progress-path">{{ progress[item.id].target_path }}</small>
              <small v-if="speedLabel(item)" class="dependency-progress-speed">{{ speedLabel(item) }}</small>
            </template>
            <template v-else-if="item.status === 'ready' && item.kind === 'script'">
              <span class="dependency-ready-check"><Check :size="14" />已就绪</span>
            </template>
          </div>
        </div>
      </li>
    </ul>

    <p class="dependency-footnote">自动安装优先使用国内可直连的镜像或通道，失败时会自动尝试官方源；不需要手动配置代理。带「打开官方下载页」的项目是第三方软件，按页面说明安装后回到这里「重新检查」。</p>
  </div>
</template>
