<script setup>
import SelectField from './SelectField.vue'
import * as executionApi from '../services/executionApi.js'

import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { selectLocalPath } from '../services/localPicker.js'
import ExecutionPolicy from './ExecutionPolicy.vue'
const props = defineProps({ initialProjectId: String, projects: { type: Array, default: () => [] } })
const projectId = ref(''), busy = ref(false), notice = ref(''), windows = ref([])
const loadedProjectId = ref(''), loadError = ref('')
const capabilityDefaults = ref(null), defaultsBusy = ref(false), defaultsError = ref('')
const defaultCapabilities = [
  { key: 'read_enabled', label: '读取文件', detail: '查看与搜索项目文件。' },
  { key: 'write_enabled', label: '修改文件', detail: '编辑项目内的源码与文本。' },
  { key: 'execute_enabled', label: '运行命令', detail: '使用终端、测试与构建。' },
  { key: 'desktop_enabled', label: '电脑操作', detail: '操作已选定的电脑窗口。' },
]
let defaultsVersion = 0
const config = reactive({ code: { root: '', read_enabled: false, write_enabled: false, execute_enabled: false, revision: 0, verification: {} }, desktop: { enabled: false, windows: [], executables: [], revision: 0 } })
const commands = reactive({ test: '', build: '', lint: '', typecheck: '' }), executableText = ref('')
let version = 0
const saved = ref('')
const hasRoot=computed(()=>Boolean(config.code.root.trim()))
const savedRoot=computed(()=>saved.value ? JSON.parse(saved.value).config.code.root : '')
const codeReady=computed(()=>hasRoot.value && config.code.root===savedRoot.value)
const accessLevels = [
  { id:'off', label:'关闭', detail:'不读取或操作项目文件', read:false, write:false, execute:false },
  { id:'read', label:'仅查看', detail:'读取与搜索项目文件', read:true, write:false, execute:false },
  { id:'edit', label:'允许编辑', detail:'读取文件，修改需要逐次确认', read:true, write:true, execute:false },
  { id:'run', label:'允许编辑与运行', detail:'修改和命令都需要逐次确认', read:true, write:true, execute:true },
]
const accessLevel = computed(() => config.code.execute_enabled ? 'run' : config.code.write_enabled ? 'edit' : config.code.read_enabled ? 'read' : 'off')
async function setAccessLevel(level) { if (busy.value || !codeReady.value) return; config.code.read_enabled=level.read; config.code.write_enabled=level.write; config.code.execute_enabled=level.execute; await save('code') }
function snapshot() { return JSON.stringify({config,commands,executableText:executableText.value}) }
const dirty = computed(() => loadedProjectId.value === projectId.value && saved.value && snapshot() !== saved.value)
function preferredProject() { return [props.initialProjectId,localStorage.getItem('mio_execution_project'),props.projects[0]?.id].find(id => props.projects.some(p => p.id===id)) || '' }
function rememberDraft() { if (dirty.value) sessionStorage.setItem(`mio_execution_draft_${projectId.value}`,snapshot()) }
onBeforeUnmount(() => { rememberDraft(); ++version; ++defaultsVersion; window.removeEventListener('mio-execution-defaults-changed', refreshDefaults) })
watch([config,commands,executableText],rememberDraft,{deep:true})
async function load() {
  const id = projectId.value, n = ++version
  loadedProjectId.value = ''; loadError.value = ''; windows.value = []
  if (!id) return
  busy.value = true; notice.value = ''
  try { const data = await executionApi.loadProject(id, {}); if (n !== version || id !== projectId.value) return
    Object.assign(config, data); for (const key of Object.keys(commands)) commands[key] = data.code.verification[key] || ''
    executableText.value = data.desktop.executables.join('\n')
    loadedProjectId.value = id
    saved.value = snapshot()
    const draft = sessionStorage.getItem(`mio_execution_draft_${id}`)
    if (draft) {
      try {
        const previous=JSON.parse(draft)
        if (previous.config.code.revision === data.code.revision && previous.config.desktop.revision === data.desktop.revision) {
          // Unsaved fields may be restored, but permission switches always show server state.
          config.code.root=previous.config.code.root || data.code.root
          Object.assign(commands,previous.commands); executableText.value=previous.executableText
          config.desktop.windows=previous.config.desktop.windows
          if(snapshot()!==saved.value)notice.value='已恢复未保存的修改，请保存后生效。'
          else sessionStorage.removeItem(`mio_execution_draft_${id}`)
        } else { sessionStorage.removeItem(`mio_execution_draft_${id}`); notice.value='权限已在其他窗口更新，已读取最新配置。' }
      } catch { sessionStorage.removeItem(`mio_execution_draft_${id}`) }
    }
  } catch (error) { if (n === version) loadError.value = error.message }
  finally { if (n === version) busy.value = false }
}
async function loadDefaults() {
  const n = ++defaultsVersion
  try { const result = await executionApi.loadDefaults({}); if (n === defaultsVersion) { capabilityDefaults.value = result; defaultsError.value = '' } }
  catch (error) { if (n === defaultsVersion) defaultsError.value = error.message }
}
async function changeDefault(event, key) {
  if (defaultsBusy.value || !capabilityDefaults.value) return
  defaultsBusy.value = true; defaultsError.value = ''
  const changes = { [key]: event.target.checked }
  if (key === 'read_enabled' && !changes[key]) Object.assign(changes, { write_enabled: false, execute_enabled: false })
  if (['write_enabled', 'execute_enabled'].includes(key) && changes[key]) changes.read_enabled = true
  try {
    capabilityDefaults.value = await executionApi.saveDefaults({ ...changes, expected_revision: capabilityDefaults.value.revision }, {})
    window.dispatchEvent(new Event('mio-execution-defaults-changed'))
  } catch (error) { await loadDefaults(); defaultsError.value = error.message }
  finally { event.target.checked = Boolean(capabilityDefaults.value?.[key]); defaultsBusy.value = false }
}
function refreshDefaults() {
  if (!defaultsBusy.value) void loadDefaults()
  if (projectId.value && !busy.value && !dirty.value) void load()
}
async function save(kind) {
  if (!projectId.value || loadedProjectId.value !== projectId.value || loadError.value || busy.value) return false
  const id = projectId.value, rootChanged = kind === 'code' && config.code.root !== savedRoot.value
  busy.value = true; notice.value = ''
  try {
    const body = kind === 'code' ? { root: config.code.root, read_enabled: config.code.read_enabled, write_enabled: config.code.write_enabled,
      execute_enabled: config.code.execute_enabled, verification: { ...commands }, expected_revision: config.code.revision }
      : { enabled: config.desktop.enabled, windows: config.desktop.windows, executables: executableText.value.split('\n').map(s => s.trim()).filter(Boolean), expected_revision: config.desktop.revision }
    if (rootChanged) Object.assign(body, { read_enabled: false, write_enabled: false, execute_enabled: false })
    const result = await executionApi.saveProject(id, kind, body, {})
    if (id === projectId.value) {
      config[kind] = result
      const baseline=JSON.parse(saved.value); baseline.config[kind]=result
      if (kind==='code') baseline.commands={...commands}
      if (kind==='desktop') baseline.executableText=executableText.value
      saved.value=JSON.stringify(baseline)
      sessionStorage.removeItem(`mio_execution_draft_${id}`); rememberDraft()
      notice.value = rootChanged
        ? '项目目录已保存；原目录权限未迁移，请为新目录重新授权。'
        : '权限已保存，重新打开仍会保留。'
    }
    return true
  } catch (error) {
    if (id === projectId.value) {
      const baseline=JSON.parse(saved.value)
      if(kind==='code')for(const key of ['read_enabled','write_enabled','execute_enabled'])config.code[key]=baseline.config.code[key]
      else {config.desktop.enabled=baseline.config.desktop.enabled;config.desktop.windows=baseline.config.desktop.windows}
      notice.value = `未保存：${error.message}`
    }
    return false
  }
  finally { busy.value = false }
}
async function chooseRoot() { try { const result = await selectLocalPath('directory'); if (result) {config.code.root = result.path;await save('code')} } catch (error) { notice.value = error.message } }
async function saveAll() {
  const baseline=JSON.parse(saved.value)
  const codeChanged=JSON.stringify({code:config.code,commands})!==JSON.stringify({code:baseline.config.code,commands:baseline.commands})
  if(codeChanged && !hasRoot.value){notice.value='请选择项目文件夹，代码权限没有保存。';return}
  if (codeChanged && !await save('code')) return
  await save('desktop')
}
async function refreshWindows() { busy.value = true; try { windows.value = (await executionApi.loadDesktopWindows({timeoutMs: 45000})).windows } catch (error) { notice.value = error.message } finally { busy.value = false } }
async function toggleWindow(item, checked) { config.desktop.windows = checked ? [...config.desktop.windows.filter(w => w.hwnd !== item.hwnd), item] : config.desktop.windows.filter(w => w.hwnd !== item.hwnd);await save('desktop') }
async function toggleCode(key) {
  if (key==='read_enabled' && !config.code.read_enabled) {config.code.write_enabled=false;config.code.execute_enabled=false}
  if (key!=='read_enabled' && config.code[key]) config.code.read_enabled=true
  if (!codeReady.value) {
    const baseline=JSON.parse(saved.value);for(const name of ['read_enabled','write_enabled','execute_enabled'])config.code[name]=baseline.config.code[name]
    notice.value='请先保存项目文件夹，再启用权限。'; return
  }
  await save('code')
}
watch(projectId,(id,old) => { if (old && loadedProjectId.value===old && saved.value && snapshot()!==saved.value) sessionStorage.setItem(`mio_execution_draft_${old}`,snapshot()); localStorage.setItem('mio_execution_project',id); void load() })
watch(() => props.projects, () => { if (!projectId.value) projectId.value = preferredProject() })
onMounted(() => { projectId.value = preferredProject(); void loadDefaults(); window.addEventListener('mio-execution-defaults-changed', refreshDefaults) })
</script>
<template>
  <section class="execution-settings">
    <ExecutionPolicy />
    <section class="execution-defaults"><h2>默认能力</h2><p class="execution-muted">新项目和未单独设置的项目自动使用这些能力；项目单独设置优先。</p>
      <label v-for="item in defaultCapabilities" :key="item.key" class="execution-row"><span><strong>{{ item.label }}</strong><small>{{ item.detail }}</small></span><input type="checkbox" :aria-label="`默认${item.label}`" :checked="Boolean(capabilityDefaults?.[item.key])" :disabled="defaultsBusy || !capabilityDefaults" @change="changeDefault($event, item.key)" /></label>
      <p class="execution-muted">使用项目绑定的文件夹。电脑操作仍需选择窗口，执行方式由上方权限控制。</p>
      <p v-if="defaultsError" class="execution-defaults-error" role="alert">{{ defaultsError }} <button type="button" :disabled="defaultsBusy" @click="loadDefaults">重试</button></p>
    </section>
    <header class="execution-header"><div><h1>目录与窗口</h1><p>选择要交给 Mio 使用的项目目录和电脑窗口。</p></div><button v-if="projectId && !loadError" type="button" :disabled="busy" @click="load">重新读取权限</button></header>
    <label class="execution-project">当前项目<SelectField v-model="projectId" aria-label="执行权限项目" :disabled="busy"><option value="">选择项目</option><option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}</option></SelectField></label>
    <p v-if="!projects.length">先创建项目，再绑定代码目录或授权电脑窗口。</p><p v-if="notice" class="execution-notice" role="status">{{ notice }}</p>
    <p v-if="projectId && loadError" role="alert">执行权限读取失败：{{ loadError }} <button type="button" :disabled="busy" @click="load">重新读取</button></p>
    <p v-else-if="projectId && loadedProjectId !== projectId" role="status">正在读取项目执行权限…</p>
    <template v-if="projectId && loadedProjectId === projectId && !loadError">
      <section class="execution-group"><h2>项目文件与终端</h2>
        <p class="execution-muted">{{ config.code.permission_source === 'global' ? '正在使用默认文件能力，修改本项目开关后会单独保存。' : '此项目使用单独保存的文件能力。' }}</p>
        <label class="execution-directory">项目文件夹<div class="execution-path"><input v-model="config.code.root" aria-label="本机项目目录" :disabled="busy" placeholder="选择项目所在的文件夹" /><button type="button" :disabled="busy" @click="chooseRoot">选择目录</button></div></label>
        <div v-if="config.code.root !== savedRoot" class="execution-path-actions"><span>目录尚未保存</span><button type="button" :disabled="busy || !hasRoot" @click="save('code')">保存项目目录</button></div>
        <p v-if="!hasRoot" class="execution-notice">此项目还没有连接电脑文件夹。选择目录后，才能读取、修改和运行代码。</p>
        <details class="execution-advanced"><summary>高级：项目能力授权</summary><p class="execution-muted">只查看会阻止修改、终端与电脑动作。其他档位仍受下面的目录和窗口授权约束。</p>
        <label class="execution-row"><span><strong>读取文件</strong><small>查看和搜索项目中的文本、源码。</small></span><input v-model="config.code.read_enabled" type="checkbox" aria-label="读取与搜索项目文本" :disabled="busy || !codeReady" @change="toggleCode('read_enabled')" /></label>
        <label class="execution-row"><span><strong>修改代码</strong><small>提出文件修改，按任务审批后写入；保留恢复记录。</small></span><input v-model="config.code.write_enabled" type="checkbox" aria-label="允许提出代码修改与恢复" :disabled="busy || !codeReady" @change="toggleCode('write_enabled')" /></label>
        <label class="execution-row"><span><strong>运行命令</strong><small>在项目目录执行终端命令、测试和构建。</small></span><input v-model="config.code.execute_enabled" type="checkbox" aria-label="允许提出本机终端命令" :disabled="busy || !codeReady" @change="toggleCode('execute_enabled')" /></label>
        </details>
        <details class="execution-advanced"><summary>测试与构建命令（可选）</summary><p class="execution-muted">用于项目验证的默认命令；留空不影响 Agent 提出其他终端命令。</p><label v-for="(label,key) in {test:'测试命令',build:'构建命令',lint:'代码检查',typecheck:'类型检查'}" :key="key">{{ label }}<input v-model="commands[key]" :aria-label="label" :disabled="busy" placeholder="填写此项目实际使用的命令" /></label><button type="button" :disabled="busy || !hasRoot" @click="save('code')">保存代码权限</button></details>
        <p class="execution-muted">终端命令在本机执行，工作目录不提供系统沙箱。</p>
      </section>
      <section class="execution-group"><h2>电脑窗口</h2>
        <p class="execution-muted">{{ config.desktop.permission_source === 'global' ? '正在使用默认电脑能力，窗口授权与开关会为此项目单独保存。' : '此项目使用单独保存的电脑权限。' }}</p>
        <label class="execution-row"><span><strong>启用电脑操作</strong><small>观察并操作你选定的 Windows 窗口。</small></span><input v-model="config.desktop.enabled" aria-label="启用此项目的电脑操作" :disabled="busy || config.desktop.available === false" type="checkbox" @change="save('desktop')" /></label>
        <template v-if="config.desktop.enabled"><div class="execution-window-heading"><span>{{ config.desktop.windows.length ? `已授权 ${config.desktop.windows.length} 个窗口` : '尚未选择窗口' }}</span><button type="button" :disabled="busy" @click="refreshWindows">读取当前窗口</button></div>
        <p v-if="!config.desktop.windows.length" class="execution-muted">启用开关后，还需选择要操作的窗口。窗口关闭后需重新选择。</p>
        <div class="execution-window-list"><label v-for="item in windows" :key="`${item.hwnd}-${item.created}`" class="execution-row"><span>{{ item.title }}<small>进程 {{ item.pid }}</small></span><input type="checkbox" :disabled="busy" :checked="config.desktop.windows.some(w => w.hwnd === item.hwnd && w.created === item.created)" @change="toggleWindow(item,$event.target.checked)" /></label></div>
        <p class="execution-muted">窗口截图提供给支持视觉的当前模型；可在对话中停止并接管。</p></template>
        <details class="execution-advanced"><summary>允许启动的软件（可选）</summary><label>允许启动的 EXE 路径（每行一个）<textarea v-model="executableText" aria-label="允许启动的 EXE 路径（每行一个）" :disabled="busy" rows="3" placeholder="填写软件的完整 EXE 路径" /></label><button type="button" :disabled="busy" @click="save('desktop')">保存电脑权限</button></details>
      </section>
      <div v-if="dirty" class="execution-save-bar" role="status"><span>目录或命令有未保存修改</span><button type="button" :disabled="busy" @click="saveAll">保存全部权限</button></div>
    </template>
  </section>
</template>
<style scoped>
.execution-defaults{margin:24px 0 30px;padding:18px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border)}.execution-defaults h2{font-size:calc(16px * var(--fs,1));margin:0 0 8px}.execution-defaults .execution-row:last-of-type{border-bottom:0}.execution-defaults-error{font-size:12px;color:var(--danger)}
.execution-header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.execution-header h1{margin:0 0 8px}.execution-header p,.execution-muted{font-size:12px;color:var(--text-muted);line-height:1.7}.execution-header p{margin:0}.execution-group{border:1px solid var(--border);border-radius:12px;margin:22px 0;padding:0 18px 12px}.execution-group h2{margin:20px 0 16px}.execution-row{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:16px 0;border-bottom:1px solid var(--border);margin:0}.execution-row>span{min-width:0;overflow-wrap:anywhere}.execution-row strong{font-size:13px;font-weight:500}.execution-row small{display:block;color:var(--text-muted);font-size:12px;margin-top:5px;line-height:1.6}.execution-row input{flex:none}.execution-directory,.execution-advanced label{display:grid;gap:8px;font-size:13px}.execution-directory{margin-bottom:8px}.execution-settings input:not([type=checkbox]),.execution-settings textarea,.execution-settings select{font:inherit;color:var(--text);background:var(--surface);border:1px solid var(--border);border-radius:7px;padding:9px 11px;min-height:38px}.execution-settings button{font:inherit;min-height:34px;padding:7px 11px;border:1px solid var(--border);border-radius:7px;background:var(--surface);color:var(--text-muted);font-size:12px}.execution-settings button:disabled,.execution-row input:disabled{opacity:.45;cursor:default}.execution-advanced{margin:16px 0 4px;font-size:12px}.execution-advanced summary{cursor:pointer;color:var(--text-muted)}.execution-advanced label{margin:14px 0}.execution-window-heading,.execution-path-actions{display:flex;justify-content:space-between;align-items:center;gap:10px;margin:15px 0;font-size:12px}.execution-notice{background:var(--surface-soft);border-radius:7px;padding:10px 12px;font-size:12px;line-height:1.7}.execution-window-list{max-height:300px;overflow:auto}.execution-save-bar{font-size:12px;justify-content:space-between}
</style>

<style scoped>
.execution-access-levels{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:16px 0}.execution-access-levels button{display:grid;gap:6px;text-align:left;padding:12px;border:1px solid var(--border);border-radius:9px;background:var(--surface);color:var(--text);cursor:pointer;min-width:0}.execution-access-levels button.active{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}.execution-access-levels small{color:var(--text-muted);font-size:12px;line-height:1.5}.execution-access-levels strong{font-size:12px}.execution-access-levels button:disabled{opacity:.5;cursor:default}@media(max-width:480px){.execution-access-levels{grid-template-columns:1fr}}
</style>
