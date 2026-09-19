<script setup>
import { ref } from 'vue'
import { useUpdates } from '../composables/useUpdates.js'
const { state, error, available, working, progress, invoke } = useUpdates()
const confirmInstall = ref(false)
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
      <div class="settings-list">
        <div class="settings-item"><span><strong>当前版本 {{ state.current_version }}</strong><small>{{ state.channel === 'stable' ? '正式版通道' : '开发版通道，与公开版分开' }}</small></span><button type="button" :disabled="working || state.privacy_paused" @click="invoke('check_updates')">检查更新</button></div>
        <label class="settings-item"><span><strong>自动检查更新</strong><small>启动后及持续运行时检查，不调用聊天模型；修改立即生效</small></span><input type="checkbox" :checked="state.auto_check" :disabled="working" @change="invoke('configure_updates', { auto_check: $event.target.checked })" /></label>
        <label class="settings-item"><span><strong>自动下载新版本</strong><small>下载完成后仍由你确认安装，不会突然中断聊天</small></span><input type="checkbox" :checked="state.auto_download" :disabled="working" @change="invoke('configure_updates', { auto_download: $event.target.checked })" /></label>
      </div>
      <p v-if="!state.configured" class="update-note">此构建尚未启用公开更新源。开发版不会自动切换成公开版。</p>
      <p v-if="state.privacy_paused" class="update-note">隐私总控已暂停外部连接，更新检查和下载也已暂停。</p>
      <div class="update-status" role="status" aria-live="polite">
        <strong>{{ state.message }}</strong>
        <p v-if="error" role="alert">{{ error }}</p>
        <p v-if="state.last_result?.message">上次升级：{{ state.last_result.message }}</p>
      </div>
      <div v-if="state.release" class="update-release">
        <strong>版本 {{ state.release.version }} · {{ size(state.release.size) }}</strong>
        <p class="update-notes">{{ state.release.notes }}</p>
      </div>
      <div v-if="state.state === 'downloading'" class="update-progress">
        <progress :value="progress" max="100" aria-label="更新下载进度" />
        <span>{{ size(state.downloaded) }} / {{ size(state.total) }} · {{ Math.floor(progress) }}%</span>
      </div>
      <div class="update-actions">
        <button v-if="state.release && ['available', 'failed', 'cancelled'].includes(state.state)" type="button" :disabled="working || state.privacy_paused" @click="invoke('download_update')">{{ state.downloaded ? '继续下载' : '下载更新' }}</button>
        <button v-if="['checking', 'downloading'].includes(state.state)" type="button" @click="invoke('cancel_update')">取消</button>
        <button v-if="state.state === 'ready' && !confirmInstall" class="primary-button" type="button" :disabled="working || !state.installed || state.privacy_paused" @click="confirmInstall = true">安装并重启</button>
      </div>
      <div v-if="confirmInstall" class="update-confirm" role="group" aria-label="确认安装更新">
        <p>将备份当前数据、关闭 Mio 并安装新版本。未完成的任务请先完成或取消，聊天、日记和设置会保留。</p>
        <button type="button" @click="confirmInstall = false">暂不安装</button>
        <button class="primary-button" type="button" :disabled="working" @click="install">确认安装并重启</button>
      </div>
      <p class="update-note">更新检查独立于 AI 联网搜索。只联系版本发布服务，不上传聊天、日记或模型密钥。</p>
    </template>
  </section>
</template>

<style scoped>
.update-panel { min-width: 0; }
.update-note { font-size: 12px; opacity: .72; line-height: 1.7; }
.update-status { margin: 14px 0; overflow-wrap: anywhere; }
.update-status p { margin: 7px 0; }
.update-release { padding: 12px; border: 1px solid var(--line, #dbe4e5); border-radius: 10px; }
.update-notes { white-space: pre-wrap; line-height: 1.7; overflow-wrap: anywhere; max-height: 260px; overflow: auto; }
.update-actions { display: flex; gap: 10px; margin: 12px 0; flex-wrap: wrap; }
.update-progress { display: grid; gap: 6px; margin: 14px 0; }
.update-progress progress { width: 100%; }
.update-confirm { border: 1px solid var(--line, #dbe4e5); border-radius: 10px; padding: 14px; }
.update-confirm p { line-height: 1.7; }
.update-confirm button { margin-right: 10px; }
</style>
