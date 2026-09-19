<script setup>
import { computed, ref } from 'vue'
import { ArrowDownToLine, RefreshCw, ShieldCheck, PackageCheck, Info } from '@lucide/vue'
import { useUpdates } from '../composables/useUpdates.js'
const { state, error, available, working, progress, invoke } = useUpdates()
const confirmInstall = ref(false)
const statusMessage = computed(() => {
  if (state.value.privacy_paused) return '外部连接已暂停，恢复后可检查更新'
  if (state.value.configured === false && !error.value) return '此开发版本暂未开放在线更新'
  return state.value.message
})
const size = bytes => `${(Number(bytes || 0) / 1048576).toFixed(1)} MB`
async function install() {
  confirmInstall.value = false
  await invoke('install_update', true)
}
</script>

<template>
  <section class="settings-section-block update-panel" aria-label="应用更新">
    <h2>应用更新</h2>
    <p v-if="!available" class="update-note">请在 Mio 桌面应用中检查更新。网页预览不能安装程序。</p>
    <template v-else>
      <div class="update-version-card">
        <div class="update-version-icon"><PackageCheck :size="24" /></div>
        <div class="update-version-copy"><span>当前版本 <b>{{ state.channel === 'stable' ? '正式版' : '开发版' }}</b></span><strong>{{ state.current_version || '读取中…' }}</strong><small>{{ state.configured === false ? '新版本准备好后，可使用安装包升级' : '让 Mio 保持最新状态' }}</small></div>
        <button class="update-check" type="button" :disabled="working || state.privacy_paused || state.configured === false" @click="invoke('check_updates')"><RefreshCw :size="15" :class="{ spin: state.state === 'checking' }" />{{ state.state === 'checking' ? '正在检查' : '检查更新' }}</button>
      </div>
      <div class="settings-list update-preferences">
        <label class="settings-item"><span><strong>自动检查更新</strong><small>启动后自动检查新版本</small></span><input class="update-switch" type="checkbox" role="switch" :checked="state.auto_check" :disabled="working || state.configured === false" @change="invoke('configure_updates', { auto_check: $event.target.checked })" /></label>
        <label class="settings-item"><span><strong>自动下载新版本</strong><small>下载完成后，由你决定何时安装</small></span><input class="update-switch" type="checkbox" role="switch" :checked="state.auto_download" :disabled="working || state.configured === false" @change="invoke('configure_updates', { auto_download: $event.target.checked })" /></label>
      </div>
      <div class="update-status" :class="{ 'has-error': error || state.state === 'failed' }" role="status" aria-live="polite">
        <Info :size="16" /><div><span>{{ statusMessage }}</span><p v-if="error" role="alert">{{ error }}</p><p v-if="state.last_result?.message">上次升级：{{ state.last_result.message }}</p></div>
      </div>
      <div v-if="state.release" class="update-release">
        <strong>版本 {{ state.release.version }} · {{ size(state.release.size) }}</strong>
        <p class="update-notes">{{ state.release.notes }}</p>
      </div>
      <div v-if="state.state === 'downloading'" class="update-progress">
        <progress :value="progress" max="100" aria-label="更新下载进度" />
        <span>{{ size(state.downloaded) }} / {{ size(state.total) }} · {{ Math.floor(progress) }}%</span>
      </div>
      <div v-if="state.release || ['checking', 'downloading'].includes(state.state)" class="update-actions">
        <button v-if="state.release && ['available', 'failed', 'cancelled'].includes(state.state)" type="button" :disabled="working || state.privacy_paused" @click="invoke('download_update')"><ArrowDownToLine :size="15" />{{ state.downloaded ? '继续下载' : '下载更新' }}</button>
        <button v-if="['checking', 'downloading'].includes(state.state)" type="button" @click="invoke('cancel_update')">取消</button>
        <button v-if="state.state === 'ready' && !confirmInstall" class="primary-button" type="button" :disabled="working || !state.installed || state.privacy_paused" @click="confirmInstall = true">安装并重启</button>
      </div>
      <div v-if="confirmInstall" class="update-confirm" role="group" aria-label="确认安装更新">
        <p>将备份当前数据、关闭 Mio 并安装新版本。未完成的任务请先完成或取消，聊天、日记和设置会保留。</p>
        <button type="button" @click="confirmInstall = false">暂不安装</button>
        <button class="primary-button" type="button" :disabled="working" @click="install">确认安装并重启</button>
      </div>
      <p class="update-note update-privacy"><ShieldCheck :size="15" />更新检查不会上传你的聊天、日记或模型密钥</p>
    </template>
  </section>
</template>

<style scoped>
.update-panel { min-width: 0; }
.update-panel button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-height: 36px; padding: 8px 14px; border: 1px solid var(--line); border-radius: 8px; background: #fff; color: var(--ink); font: inherit; font-size: 12px; cursor: pointer; }
.update-panel button:hover:not(:disabled) { background: var(--accent-soft); border-color: var(--accent); }
.update-panel button:disabled { opacity: .5; cursor: default; }
.update-panel button:focus-visible,.update-switch:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.update-panel .primary-button { background: var(--accent); border-color: var(--accent); color: #fff; }
.update-version-card { display: flex; align-items: center; gap: 16px; padding: 22px; margin-bottom: 14px; border: 1px solid var(--line); border-radius: 13px; background: linear-gradient(115deg, #fff, var(--surface-soft)); }
.update-version-icon { display: grid; place-items: center; flex: 0 0 48px; height: 48px; border-radius: 14px; background: var(--accent-soft); color: var(--accent-dark); }
.update-version-copy { display: grid; gap: 5px; flex: 1; min-width: 0; }
.update-version-copy > span { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); }
.update-version-copy b { padding: 2px 7px; font-size: 10px; font-weight: 500; border-radius: 5px; background: var(--accent-soft); color: var(--accent-dark); }
.update-version-copy > strong { font-size: 23px; letter-spacing: .3px; overflow-wrap: anywhere; }
.update-version-copy > small { font-size: 11px; color: var(--muted); line-height: 1.6; }
.update-preferences { border-top: 1px solid var(--line); border-radius: 12px; }
.update-preferences .settings-item { min-height: 74px; gap: 18px; padding: 15px 20px; cursor: pointer; }
.update-preferences .settings-item > span { min-width: 0; }
.update-preferences .settings-item small { line-height: 1.6; }
.update-preferences input.update-switch { appearance: none; position: relative; flex: 0 0 36px; width: 36px; min-width: 36px; height: 21px; min-height: 21px; padding: 0; margin: 0; border: 0; border-radius: 20px; background: #c6cdd0; cursor: pointer; transition: background .15s; }
.update-preferences input.update-switch::after { content: ''; position: absolute; top: 3px; left: 3px; width: 15px; height: 15px; border-radius: 50%; background: white; box-shadow: 0 1px 3px #0002; transition: transform .15s; }
.update-preferences input.update-switch:checked { background: var(--accent); }
.update-preferences input.update-switch:checked::after { transform: translateX(15px); }
.update-preferences input.update-switch:disabled { opacity: .45; cursor: default; }
.update-note { font-size: 11px; color: var(--muted); line-height: 1.7; }
.update-privacy { display: flex; align-items: center; gap: 7px; margin-top: 20px; }
.update-privacy svg { flex-shrink: 0; }
.update-status { display: flex; align-items: flex-start; gap: 9px; padding: 13px 15px; margin: 14px 0; border-radius: 9px; background: var(--surface-soft); color: var(--muted); font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }
.update-status > svg { flex-shrink: 0; margin-top: 2px; }
.update-status.has-error { background: #fff1ee; color: #a24638; }
.update-status p { margin: 7px 0 0; }
.update-release { padding: 16px; border: 1px solid var(--line); border-radius: 10px; font-size: 13px; }
.update-notes { white-space: pre-wrap; line-height: 1.7; overflow-wrap: anywhere; max-height: 260px; overflow: auto; }
.update-actions { display: flex; gap: 10px; margin: 12px 0; flex-wrap: wrap; }
.update-progress { display: grid; gap: 8px; margin: 18px 0; font-size: 11px; color: var(--muted); }
.update-progress progress { appearance: none; width: 100%; height: 6px; border: 0; border-radius: 8px; overflow: hidden; }
.update-progress progress::-webkit-progress-bar { background: var(--line); }
.update-progress progress::-webkit-progress-value { background: var(--accent); border-radius: 8px; }
.update-confirm { border: 1px solid var(--line); border-radius: 10px; padding: 16px; font-size: 12px; }
.update-confirm p { line-height: 1.8; margin-top: 0; }
.update-confirm button { margin: 4px 10px 0 0; }
@media (max-width: 620px) {
  .update-version-card { flex-wrap: wrap; padding: 16px; gap: 12px; }
  .update-version-copy { flex-basis: calc(100% - 60px); }
  .update-check { width: 100%; }
}
</style>
