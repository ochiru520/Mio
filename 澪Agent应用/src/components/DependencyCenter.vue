<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Check, CheckCircle2, CircleAlert, CircleHelp, Download, ExternalLink, RefreshCw, RotateCw, Settings2, Wrench } from '@lucide/vue'
import { activateLocalVision, verifyDependency, installDependency, loadDependencies, loadDependencyStatus } from '../services/dependenciesApi.js'

const props = defineProps({
  compact: { type: Boolean, default: false },
})
const emit = defineEmits(['refresh-environment', 'navigate'])

const busy = ref(false)
const error = ref('')
const dependencies = ref([])
const progress = ref({})
const pollTimer = ref(null)
const activating = ref(false)
const notice = ref('')

const statusMeta = {
  ready: { label: '已就绪', icon: CheckCircle2, tone: 'ok' },
  configured: { label: '已配置', icon: CheckCircle2, tone: 'ok' },
  unconfigured: { label: '未配置', icon: CircleHelp, tone: 'hint' },
  missing: { label: '缺失', icon: CircleAlert, tone: 'warn' },
  installed: { label: '已安装 · 未启动', icon: CheckCircle2, tone: 'hint' },
  unverified: { label: '文件已安装 · 待验证', icon: CircleHelp, tone: 'hint' },
  degraded: { label: '已安装 · 暂不可用', icon: CircleAlert, tone: 'warn' },
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
    if (activeInstalls().length) startPolling()
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

async function install(item, offline = false) {
  if (busy.value || item.installing) return
  busy.value = true
  error.value = ''
  notice.value = ''
  let alreadyInstalled = false
  try {
    let started
    if (offline) {
      const picker = window.pywebview?.api?.install_dependency_package
      if (!picker) throw new Error('请在 Mio 桌面窗口中选择离线包。当前版本暂未提供此组件的在线下载包。')
      started = await picker(item.id)
      if (started?.canceled) return
      if (!started?.ok) throw new Error(started?.error || '离线包安装启动失败')
    } else started = await installDependency(item.id)
    if (started.installing === false) {
      notice.value = started.message
      alreadyInstalled = true
      return
    }
    item.installing = true
    item.last_error = ''
    progress.value[item.id] = { installing: true, stage: 'starting', percent: 0, message: started.message || '正在启动安装…' }
    startPolling()
  } catch (err) {
    error.value = err.message || '安装启动失败'
  } finally {
    busy.value = false
    if (alreadyInstalled) await refresh()
  }
}

async function activateVision() {
  if (busy.value || activating.value) return
  activating.value = true
  busy.value = true
  notice.value = ''
  error.value = ''
  try {
    const result = await activateLocalVision()
    if (result.status === 'available') notice.value = result.detail
    else notice.value = `文件已安装，当前验证未通过：${result.detail}`
  } catch (err) { notice.value = `本地视觉启动失败：${err.message}` }
  finally {
    activating.value = false
    busy.value = false
    await refresh()
  }
}

async function verify(item) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  notice.value = '正在加载本地模型验证，请稍候…'
  try {
    const result = await verifyDependency(item.id)
    notice.value = result.detail
  } catch (err) { error.value = err.message || '验证失败' }
  finally { busy.value = false; await refresh() }
}

function openUrl(url) {
  if (url) window.open(url, '_blank', 'noopener')
}

function actionFor(item) {
  if (item.id === 'ollama_vision' && ['installed', 'unverified', 'degraded'].includes(item.status)) {
    return { kind: 'activate', label: item.status === 'installed' ? '启动并验证' : '重新验证' }
  }
  if (item.installing) return { kind: 'progress' }
  if (['genie_runtime', 'whisper'].includes(item.id) && ['unverified', 'degraded'].includes(item.status)) return { kind: 'verify', label: '验证本地模型' }
  if (item.id === 'gpt_sovits' && item.status === 'unverified') return { kind: 'navigate', label: '去试听', target: 'settings-voice' }
  if (item.id === 'napcat' && item.status === 'configured') return { kind: 'navigate', label: '连接 QQ', target: 'settings-qq' }
  if (item.offline_only && item.status === 'missing') return { kind: 'offline', label: '选择离线包安装' }
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
        <small>检查这台电脑上有什么、缺什么；支持在线安装或离线包导入；本地模型可单独验证。</small>
      </div>
      <button type="button" :disabled="busy" @click="refresh">
        <RefreshCw :class="{ spin: busy }" :size="14" />重新检查
      </button>
    </div>

    <div v-if="dependencies.length" class="dependency-summary">
      <span><strong>{{ summary.ready }}</strong><small>已就绪或已配置 / {{ summary.total }} 项</small></span>
      <span v-for="item in dependencies.filter((entry) => entry.installing)" :key="item.id" class="dependency-summary-installing">
        <RotateCw class="spin" :size="12" /><small>{{ item.label }} {{ percentOf(item) }}%</small>
      </span>
    </div>

    <div v-if="error" class="dependency-error">{{ error }}</div>
    <p v-if="notice" role="status" class="dependency-notice">{{ notice }}</p>

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
            <small v-if="item.detail" class="dependency-detail">{{ item.detail }}</small>
            <small v-if="item.status !== 'ready' && item.status !== 'configured'">{{ item.missing_effect }}</small>
            <small v-if="!['installed', 'unverified', 'degraded', 'ready', 'configured'].includes(item.status)" class="dependency-how">怎么装：{{ item.how }}</small>
            <small v-if="item.offline_only" class="dependency-how">暂未提供在线包。对应文件：{{ item.package_name }}</small>
            <small v-if="item.log_path" class="dependency-path">安装日志：{{ item.log_path }}</small>
            <small v-if="item.size_label" class="dependency-size">体积：{{ item.size_label }}</small>
            <small v-if="item.install_path" class="dependency-path">一键安装位置：{{ item.install_path }}</small>
            <div v-if="item.last_error" class="dependency-last-error">上次安装：{{ item.last_error }}</div>
          </div>
          <div class="dependency-item-actions">
            <template v-if="actionFor(item).kind === 'activate'">
              <button class="primary" type="button" :disabled="busy || activating" @click="activateVision"><RefreshCw :class="{ spin: activating }" :size="14" />{{ activating ? '正在启动并验证' : actionFor(item).label }}</button>
            </template>
            <template v-else-if="actionFor(item).kind === 'verify'">
              <button type="button" :disabled="busy" @click="verify(item)">{{ actionFor(item).label }}</button>
              <button type="button" :disabled="busy" @click="install(item, item.offline_only)">修复安装</button>
            </template>
            <template v-else-if="actionFor(item).kind === 'offline'">
              <button type="button" :disabled="busy" @click="install(item, true)">选择离线包安装</button>
            </template>
            <template v-else-if="actionFor(item).kind === 'install'">
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

    <p class="dependency-footnote">在线组件会尝试可用下载源，能否连接取决于当前网络。离线组件需选择对应 ZIP 包；“已配置”不代表已连接，文件安装完成后请验证或试听。</p>
  </div>
</template>
