<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { Check, FolderOpen, RefreshCw, Save, X } from '@lucide/vue'
import { apiRequest } from '../services/api.js'
import { selectLocalPath } from '../services/localPicker.js'

const props = defineProps({ section: { type: String, default: 'budget' } })
const limits = reactive({ enabled: false, model_calls: 8, tool_calls: 20, seconds: 180, cost_yuan: 2 })
const savedLimits = reactive({ ...limits })
const roots = ref([])
const outputRoot = ref('')
const folder = ref('')
const budgetReady = ref(false)
const filesReady = ref(false)
const budgetLoading = ref(false)
const filesLoading = ref(false)
const budgetBusy = ref(false)
const filesBusy = ref(false)
const budgetError = ref('')
const filesError = ref('')
const budgetNotice = ref('')
const filesNotice = ref('')

async function loadBudget() {
  if (budgetLoading.value || budgetBusy.value) return
  budgetLoading.value = true
  budgetError.value = ''
  try {
    const data = await apiRequest('/api/agent/work?limit=1')
    if (!data.limits) throw new Error('未能读取已保存的预算。')
    Object.assign(limits, data.limits)
    Object.assign(savedLimits, limits)
    budgetReady.value = true
  } catch (cause) { budgetError.value = cause.message; budgetReady.value = false }
  finally { budgetLoading.value = false }
}
async function loadFiles() {
  if (filesLoading.value || filesBusy.value) return
  filesLoading.value = true
  filesError.value = ''
  try {
    const data = await apiRequest('/api/agent/work/file-roots')
    roots.value = data.roots || []
    outputRoot.value = data.output_root || ''
    filesReady.value = true
  } catch (cause) { filesError.value = cause.message; filesReady.value = false }
  finally { filesLoading.value = false }
}
async function saveLimits() {
  if (!budgetReady.value || budgetBusy.value) return
  budgetBusy.value = true
  budgetError.value = ''
  budgetNotice.value = ''
  try {
    Object.assign(limits, await apiRequest('/api/agent/work/limits', { method: 'PUT', body: JSON.stringify(limits) }))
    Object.assign(savedLimits, limits)
    budgetNotice.value = '预算已保存。'
  } catch (cause) { budgetError.value = cause.message }
  finally { budgetBusy.value = false }
}
async function saveRoots(paths) {
  if (!filesReady.value || filesBusy.value) return
  filesBusy.value = true
  filesError.value = ''
  filesNotice.value = ''
  try {
    const data = await apiRequest('/api/agent/work/file-roots', { method: 'PUT', body: JSON.stringify({ paths }) })
    roots.value = data.roots || []
    outputRoot.value = data.output_root || ''
    folder.value = ''
    filesNotice.value = '文件权限已保存。'
  } catch (cause) { filesError.value = cause.message }
  finally { filesBusy.value = false }
}
async function selectFolder() {
  try {
    const result = await selectLocalPath('directory')
    if (result) {
      const current = await apiRequest('/api/agent/work/file-roots')
      await saveRoots([...new Set([...(current.roots || []).filter(p => p !== current.output_root), result.path])])
    }
  } catch (cause) { filesError.value = cause.message }
}
onMounted(() => { void loadBudget(); void loadFiles() })
watch(() => props.section, section => { if (section === 'files') void loadFiles() })
</script>

<template>
  <div class="agent-task-settings">
    <section v-show="section === 'budget'" class="agent-settings-section">
      <p v-if="budgetError" role="alert" class="agent-settings-message error">{{ budgetError }}</p>
      <p v-if="budgetNotice" role="status" class="agent-settings-message">{{ budgetNotice }}</p>
      <p class="agent-setting-note">默认持续执行到完成、需要补充信息或你主动停止。连续没有进展时会暂停。</p>
      <div v-if="!budgetReady" class="agent-settings-actions"><span>{{ budgetLoading ? '正在读取预算' : '预算读取失败' }}</span><button type="button" title="重新读取预算" :disabled="budgetLoading" @click="loadBudget"><RefreshCw :size="16" /></button></div>
      <form @submit.prevent="saveLimits">
        <fieldset :disabled="!budgetReady || budgetBusy" class="agent-settings-group">
          <label class="agent-setting-row"><span>启用每轮执行限制</span><input v-model="limits.enabled" type="checkbox" /></label>
          <div v-if="limits.enabled">
          <label class="agent-setting-row"><span>模型调用次数</span><input v-model.number="limits.model_calls" required type="number" min="2" max="24" step="1" /></label>
          <label class="agent-setting-row"><span>工具调用次数</span><input v-model.number="limits.tool_calls" required type="number" min="1" max="100" step="1" /></label>
          <label class="agent-setting-row"><span>执行时限（秒）</span><input v-model.number="limits.seconds" required type="number" min="15" max="1800" step="1" /></label>
          <label class="agent-setting-row"><span>模型费用上限（元）</span><input v-model.number="limits.cost_yuan" required type="number" min="0.01" max="100" step="0.01" /></label>
          </div>
          <footer class="agent-form-footer"><button type="button" @click="Object.assign(limits, savedLimits)"><X :size="15" />取消</button><button type="submit" class="primary"><Save :size="16" />{{ budgetBusy ? '正在保存' : '保存预算' }}</button></footer>
        </fieldset>
      </form>
    </section>
    <section v-show="section === 'files'" class="agent-settings-section">
      <p v-if="filesError" role="alert" class="agent-settings-message error">{{ filesError }}</p>
      <p v-if="filesNotice" role="status" class="agent-settings-message">{{ filesNotice }}</p>
      <div v-if="!filesReady" class="agent-settings-actions"><span>{{ filesLoading ? '正在读取目录' : '目录读取失败' }}</span><button type="button" title="重新读取目录" :disabled="filesLoading" @click="loadFiles"><RefreshCw :size="16" /></button></div>
      <ul class="agent-root-list"><li v-for="root in roots" :key="root"><span>{{ root }}</span><small v-if="root === outputRoot">任务输出</small><button v-else type="button" title="撤销目录授权" :disabled="filesBusy" @click="saveRoots(roots.filter(path => path !== root && path !== outputRoot))"><X :size="16" /></button></li></ul>
      <div class="agent-settings-actions"><button type="button" class="primary" :disabled="!filesReady || filesBusy" @click="selectFolder"><FolderOpen :size="16" />选择文件夹并授权读取</button></div>
      <p class="agent-setting-note">添加后可以让澪按文件名搜索并读取这里的资料，无需手动填写每个文件路径。</p>
      <details><summary>手动填写路径</summary><form @submit.prevent="saveRoots([...roots.filter(path => path !== outputRoot), folder.trim()])">
        <fieldset :disabled="!filesReady || filesBusy">
          <label class="agent-root-input">资料目录<input v-model="folder" required placeholder="输入资料文件夹的完整路径" /></label>
          <div class="agent-settings-actions"><button type="submit" class="primary" :disabled="!folder.trim()"><Check :size="16" />{{ filesBusy ? '正在保存' : '授权读取' }}</button></div>
        </fieldset>
      </form></details>
    </section>
  </div>
</template>
