<script setup>
import * as dependenciesApi from '../services/dependenciesApi.js'

import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { Check, CheckCircle2, CircleAlert, CircleHelp, Download, ExternalLink, RefreshCw, RotateCw, Settings2, Wrench } from '../icons/index.js'
import { activateLocalVision, verifyDependency, installDependency, loadDependencies, loadDependencyStatus, previewDependencyUninstall, uninstallDependency, loadDependencyPackage, saveDependencyPackageSource } from '../services/dependenciesApi.js'

import CapabilityChecks from './CapabilityChecks.vue'

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
const uninstallPlan = ref(null)
const uninstallPanel = ref(null)
const packagePanel = ref(null)
const packageInfo = ref(null)
const packageUrls = ref('')

async function showPackage(item) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    packageInfo.value = { ...await loadDependencyPackage(item.id), label: item.label }
    packageUrls.value = packageInfo.value.urls.join('\n')
    await nextTick()
    packagePanel.value?.scrollIntoView({ block: 'nearest' })
    packagePanel.value?.focus({ preventScroll: true })
  } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}

async function savePackageSource() {
  if (busy.value || !packageInfo.value) return
  busy.value = true; error.value = ''
  const item = packageInfo.value
  try {
    packageInfo.value = { ...await saveDependencyPackageSource(item.id, packageUrls.value.split(/\r?\n/).map(url => url.trim()).filter(Boolean)), label: item.label }
    notice.value = packageInfo.value.urls.length ? '已保存下载地址；安装时会核对固定版本和完整性。' : '已恢复随程序提供的下载源。'
  } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
  const failure = error.value
  await refresh()
  if (failure) error.value = failure
}

async function prepareUninstall(item) {
  if (busy.value || item.installing) return
  busy.value = true; error.value = ''; uninstallPlan.value = null
  try {
    uninstallPlan.value = { ...await previewDependencyUninstall(item.id), label: item.label }
    await nextTick()
    uninstallPanel.value?.scrollIntoView({ block: 'nearest' })
    uninstallPanel.value?.focus({ preventScroll: true })
  }
  catch (cause) { error.value = cause.message || '无法检查卸载范围' }
  finally { busy.value = false }
}

async function confirmUninstall() {
  if (busy.value || !uninstallPlan.value) return
  busy.value = true; error.value = ''; notice.value = '正在停止相关服务并卸载，请稍候…'
  const plan = uninstallPlan.value
  try {
    const result = await uninstallDependency(plan.id, plan.token)
    notice.value = result.message
  } catch (cause) { error.value = cause.message || '卸载未完成，请重新检查'; notice.value = '' }
  finally {
    const failure = error.value
    uninstallPlan.value = null; busy.value = false
    await refresh()
    if (failure) error.value = failure
  }
}

const statusMeta = {
  ready: { label: '已就绪', icon: CheckCircle2, tone: 'ok' },
  configured: { label: '已配置', icon: CheckCircle2, tone: 'ok' },
  unconfigured: { label: '未配置', icon: CircleHelp, tone: 'hint' },
  missing: { label: '缺失', icon: CircleAlert, tone: 'warn' },
  installed: { label: '已安装 · 未启动', icon: CheckCircle2, tone: 'hint' },
  unverified: { label: '文件已安装 · 待验证', icon: CircleHelp, tone: 'hint' },
  degraded: { label: '已安装 · 暂不可用', icon: CircleAlert, tone: 'warn' },
}

async function copyDiagnostics() {
  if (busy.value) return
  busy.value = true
  try {
    const report = await dependenciesApi.loadDiagnostics({})
    await navigator.clipboard.writeText(JSON.stringify(report, null, 2))
    notice.value = `已复制脱敏诊断（${report.checked_at}）`
  } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}

async function refresh() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  dependencies.value = []
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
    progress.value = {}
    uninstallPlan.value = null
    stopPolling()
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
  finally {
    const failure = error.value
    busy.value = false
    await refresh()
    if (failure) error.value = failure
  }
}

function openUrl(url) {
  if (url) window.open(url, '_blank', 'noopener')
}

function actionFor(item) {
  if (item.installing) return { kind: 'progress' }
  if (item.id === 'ollama_vision' && ['installed', 'unverified', 'degraded', 'ready'].includes(item.status)) {
    return { kind: 'activate', label: item.status === 'installed' ? '启动并验证' : '重新验证' }
  }
  if (item.installing) return { kind: 'progress' }
  if (['genie_runtime', 'whisper'].includes(item.id) && ['unverified', 'degraded', 'ready'].includes(item.status)) return { kind: 'verify', label: item.status === 'ready' ? '重新验证' : '验证本地模型' }
  if (item.id === 'gpt_sovits' && item.status === 'unverified') return { kind: 'navigate', label: '去试听', target: 'settings-voice' }
  if (item.id === 'napcat' && item.status === 'configured') return { kind: 'navigate', label: '连接 QQ', target: 'settings-qq' }
  if (item.local_package_available && item.status === 'missing') return { kind: 'install', label: '安装本机数据包' }
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
        <small>检查这台电脑上有什么、缺什么；支持安装、验证与卸载可选模型。</small>
      </div>
      <button type="button" :disabled="busy" @click="refresh">
        <RefreshCw :class="{ spin: busy }" :size="14" />重新检查
      </button>
      <button type="button" :disabled="busy" @click="copyDiagnostics">复制脱敏诊断</button>
    </div>

    <div v-if="dependencies.length" class="dependency-summary">
      <span><strong>{{ summary.ready }}</strong><small>已就绪或已配置 / {{ summary.total }} 项</small></span>
      <span v-for="item in dependencies.filter((entry) => entry.installing)" :key="item.id" class="dependency-summary-installing">
        <RotateCw class="spin" :size="12" /><small>{{ item.label }} {{ percentOf(item) }}%</small>
      </span>
    </div>

    <div v-if="error" class="dependency-error">{{ error }}</div>
    <p v-if="notice" role="status" class="dependency-notice">{{ notice }}</p>
    <section v-if="packageInfo" ref="packagePanel" tabindex="-1" class="dependency-package-panel" aria-label="组件获取与安装">
      <div class="dependency-package-heading"><strong>{{ packageInfo.label }} · 获取与安装</strong><button type="button" @click="packageInfo = null">收起</button></div>
      <p>{{ packageInfo.message }}</p>
      <dl><dt>对应文件</dt><dd>{{ packageInfo.file_name }}</dd><dt>大小</dt><dd>{{ (packageInfo.size_bytes / 1024 / 1024).toFixed(1) }} MB</dd><dt>SHA-256</dt><dd>{{ packageInfo.sha256 }}</dd></dl>
      <p class="dependency-package-note">{{ packageInfo.license_notice }}</p>
      <button type="button" @click="openUrl(packageInfo.release_url)"><ExternalLink :size="14" />查看公开发布页</button>
      <details><summary>已有下载链接</summary><label for="dependency-package-urls">HTTPS 下载地址（每行一个）</label><textarea id="dependency-package-urls" v-model="packageUrls" :disabled="busy" rows="3" placeholder="填写发布者提供的对应版本 ZIP 链接" /><small>地址可以更换，文件版本、大小和校验值保持固定。留空恢复程序默认源。</small><button type="button" :disabled="busy" @click="savePackageSource">保存下载地址</button></details>
      <p>已有 ZIP 可点“选择离线包安装”；放在程序目录的“模型”文件夹内也会自动识别，安装前核对完整性。</p>
    </section>
    <section v-if="uninstallPlan" ref="uninstallPanel" tabindex="-1" class="dependency-uninstall-confirm" aria-label="确认卸载模型">
      <strong>卸载 {{ uninstallPlan.label }}</strong>
      <p>{{ uninstallPlan.impact }}</p>
      <p>{{ uninstallPlan.preserved }}</p>
      <p>将删除以下文件，预计释放 {{ (uninstallPlan.size_bytes / 1024 / 1024).toFixed(1) }} MB：</p>
      <ul><li v-for="path in uninstallPlan.paths" :key="path">{{ path }}</li></ul>
      <button type="button" :disabled="busy || !uninstallPlan.paths.length" @click="confirmUninstall">确认卸载</button>
      <button type="button" :disabled="busy" @click="uninstallPlan = null">取消</button>
    </section>

    <CapabilityChecks @navigate="emit('navigate', $event)" />

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
            <small v-if="item.verification?.checked_at">最近验证：{{ new Date(item.verification.checked_at).toLocaleString() }}</small>
            <small v-if="item.uninstall_blocker">{{ item.uninstall_blocker }}</small>
            <small v-if="item.status !== 'ready' && item.status !== 'configured'">{{ item.missing_effect }}</small>
            <small v-if="!['installed', 'unverified', 'degraded', 'ready', 'configured'].includes(item.status)" class="dependency-how">怎么装：{{ item.how }}</small>
            <small v-if="item.local_package_available" class="dependency-how">已找到本机数据包，安装前会核对完整性。</small>
            <small v-else-if="item.offline_only" class="dependency-how">此版本尚未提供公开下载地址。可查看获取说明或选择已有 ZIP。</small>
            <small v-if="item.log_path" class="dependency-path">安装日志：{{ item.log_path }}</small>
            <small v-if="item.size_label" class="dependency-size">体积：{{ item.size_label }}</small>
            <small v-if="item.install_path" class="dependency-path">一键安装位置：{{ item.install_path }}</small>
            <div v-if="item.last_error" class="dependency-last-error">上次安装：{{ item.last_error }}</div>
          </div>
          <div class="dependency-item-actions">
            <button v-if="['genie_runtime','gpt_sovits'].includes(item.id)" type="button" :disabled="busy || item.installing" @click="showPackage(item)">获取与安装</button>
            <button v-if="item.can_uninstall" type="button" :disabled="busy || item.installing" @click="prepareUninstall(item)">卸载</button>
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

<style scoped>
.dependency-uninstall-confirm { margin: 12px 0; padding: 14px; border: 1px solid var(--line); border-radius: 10px; overflow-wrap: anywhere; }
.dependency-uninstall-confirm p, .dependency-uninstall-confirm li { font-size: calc(12px * var(--fs, 1)); line-height: 1.6; }
.dependency-uninstall-confirm button { margin-right: 8px; padding: 7px 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); color: inherit; font: inherit; font-size: calc(12px * var(--fs, 1)); cursor: pointer; }
.dependency-uninstall-confirm button:first-of-type { color: var(--danger); border-color: var(--danger); }
.dependency-uninstall-confirm button:disabled { cursor: wait; opacity: .5; }
.dependency-package-panel { margin: 16px 0; padding: 18px; border: 1px solid var(--line); border-radius: 10px; font-size: calc(12px * var(--fs, 1)); line-height: 1.7; overflow-wrap: anywhere; }
.dependency-package-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.dependency-package-panel dl { display: grid; grid-template-columns: 72px minmax(0,1fr); gap: 5px 12px; }
.dependency-package-panel dd { margin: 0; }
.dependency-package-panel dt, .dependency-package-note, .dependency-package-panel small { color: var(--text-muted); }
.dependency-package-panel button { display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; border: 1px solid var(--line); border-radius: 6px; background: var(--surface); color: inherit; font: inherit; cursor: pointer; }
.dependency-package-panel details { margin: 16px 0; }
.dependency-package-panel summary { cursor: pointer; }
.dependency-package-panel label, .dependency-package-panel small { display: block; margin: 10px 0 6px; }
.dependency-package-panel textarea { width: 100%; box-sizing: border-box; padding: 10px; resize: vertical; border: 1px solid var(--line); border-radius: 6px; background: var(--surface); color: inherit; font: inherit; }
</style>
