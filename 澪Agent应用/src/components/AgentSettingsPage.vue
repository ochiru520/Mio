<script setup>
import { computed, inject, reactive, ref, watch } from 'vue'
import { ArrowLeft, Bot, FolderKey, Gauge, PanelsTopLeft, Save, Search, Workflow, Wrench, X } from '@lucide/vue'
import AgentTaskSettings from './AgentTaskSettings.vue'
import AgentCreationSettings from './AgentCreationSettings.vue'
import ModelStrategyPanel from './ModelStrategyPanel.vue'
import '../styles/agent-settings.css'

const emit = defineEmits(['preferences-change', 'return'])
const context = inject('mio-chat-page')
if (!context) throw new Error('Agent 设置上下文未初始化')
const categories = [
  { id: 'model', label: '模型与推理', icon: Bot, keywords: '模型 推理 思考' },
  { id: 'budget', label: '任务执行', icon: Gauge, keywords: '预算 调用 次数 费用 执行 时限' },
  { id: 'files', label: '文件权限', icon: FolderKey, keywords: '资料 目录 授权 输出' },
  { id: 'creation', label: '创作环境', icon: Wrench, keywords: 'ComfyUI 自动 检测 安装 连接 地址 Python' },
  { id: 'workflows', label: '工作流', icon: Workflow, keywords: '自定义 导入 API JSON 图片 视频 参考图 参数' },
  { id: 'appearance', label: '界面显示', icon: PanelsTopLeft, keywords: '过程 侧栏 悬停' },
]
const category = ref('model')
const search = ref('')
const visibleCategories = computed(() => categories.filter(item => `${item.label} ${item.keywords}`.toLowerCase().includes(search.value.trim().toLowerCase())))
const currentCategory = computed(() => categories.find(item => item.id === category.value))
const notice = ref('')
const draft = reactive({ model: context.selectedModel, reasoning: context.reasoningLevel })
const reasoningOptions = computed(() => {
  if (draft.model === 'auto') return [{ id: 'auto', label: '自动' }]
  const model = context.modelGroups?.flatMap(group => group.models).find(item => item.id === draft.model)
  return model?.reasoning_options?.length ? model.reasoning_options : context.activeReasoningOptions || []
})
watch(() => draft.model, () => {
  if (!reasoningOptions.value.some(item => item.id === draft.reasoning)) draft.reasoning = reasoningOptions.value[0]?.id || ''
})
const preferences = reactive({
  process: localStorage.getItem('mio_agent_process_expanded') !== 'false',
  sidebar: localStorage.getItem('mio_agent_right_sidebar_visible') !== 'false',
  hover: localStorage.getItem('mio_agent_right_sidebar_hover_expand') !== 'false',
})
const savedPreferences = reactive({ ...preferences })
const dirty = computed(() => category.value === 'model'
  ? draft.model !== context.selectedModel || draft.reasoning !== context.reasoningLevel
  : category.value === 'appearance' && JSON.stringify(preferences) !== JSON.stringify(savedPreferences))
function reset() {
  if (category.value === 'model') Object.assign(draft, { model: context.selectedModel, reasoning: context.reasoningLevel })
  else Object.assign(preferences, savedPreferences)
  notice.value = ''
}
function save() {
  if (category.value === 'model') {
    context.chooseModel(draft.model)
    context.chooseReasoning(draft.reasoning)
  } else {
    for (const [key, value] of Object.entries({ mio_agent_process_expanded: preferences.process, mio_agent_right_sidebar_visible: preferences.sidebar, mio_agent_right_sidebar_hover_expand: preferences.hover })) localStorage.setItem(key, String(value))
    Object.assign(savedPreferences, preferences)
    emit('preferences-change', { rightSidebarVisible: preferences.sidebar, rightSidebarHoverExpand: preferences.hover })
  }
  notice.value = '设置已保存。'
}
</script>

<template>
  <section class="settings-window-shell embedded-settings-shell agent-settings-shell">
    <header class="settings-window-topbar"><button type="button" title="返回应用" @click="emit('return')"><ArrowLeft :size="16" />返回应用</button><strong>Agent 设置</strong></header>
    <div class="settings-window-layout">
      <aside class="settings-window-navigation">
        <label class="settings-window-search"><Search :size="15" /><input v-model="search" type="search" placeholder="搜索设置" aria-label="搜索 Agent 设置" /></label>
        <nav aria-label="Agent 设置分类">
          <span class="settings-window-group-label">Agent</span>
          <button v-for="item in visibleCategories" :key="item.id" type="button" :title="item.label" :aria-label="item.label" :class="{ active: category === item.id }" :aria-current="category === item.id ? 'page' : undefined" @click="category = item.id; notice = ''"><component :is="item.icon" :size="16" /><span>{{ item.label }}</span></button>
          <p v-if="!visibleCategories.length" class="agent-settings-empty">没有匹配的设置</p>
        </nav>
      </aside>
      <main class="settings-window-content agent-settings-main">
        <header class="settings-page-header"><div class="settings-page-title"><component :is="currentCategory.icon" :size="25" /><h1>{{ currentCategory.label }}</h1></div><span v-if="dirty" class="settings-unsaved">未保存</span></header>
        <div class="agent-settings-body">
          <section v-show="category === 'model'" class="agent-settings-section">
            <p v-if="notice" role="status" class="agent-settings-message">{{ notice }}</p>
            <div class="agent-settings-group">
              <label class="agent-setting-row"><span>Agent 模型</span><select v-model="draft.model" aria-label="Agent 模型"><option value="auto">自动选择</option><optgroup v-for="group in context.modelGroups" :key="group.provider_id" :label="group.provider"><option v-for="model in group.models" :key="model.id" :value="model.id">{{ model.display_name || model.model }}</option></optgroup></select></label>
              <label class="agent-setting-row"><span>推理强度</span><select v-model="draft.reasoning" aria-label="推理强度" :disabled="!reasoningOptions.length"><option v-if="!reasoningOptions.length" value="">当前模型不支持</option><option v-for="item in reasoningOptions" :key="item.id" :value="item.id">{{ item.label }}</option></select></label>
            </div>
            <ModelStrategyPanel :modes="['agent']" />
          </section>
          <AgentTaskSettings v-show="category === 'budget' || category === 'files'" :section="category" />
          <AgentCreationSettings v-show="category === 'creation' || category === 'workflows'" :section="category" />
          <section v-show="category === 'appearance'" class="agent-settings-section">
            <p v-if="notice" role="status" class="agent-settings-message">{{ notice }}</p>
            <div class="agent-settings-group">
              <label class="agent-setting-row"><span>默认展开执行过程</span><input v-model="preferences.process" type="checkbox" /></label>
              <label class="agent-setting-row"><span>显示右侧状态栏</span><input v-model="preferences.sidebar" type="checkbox" /></label>
              <label class="agent-setting-row"><span>悬停展开右侧状态栏</span><input v-model="preferences.hover" type="checkbox" :disabled="!preferences.sidebar" /></label>
            </div>
          </section>
        </div>
        <footer v-if="category === 'model' || category === 'appearance'" class="agent-form-footer"><button type="button" :disabled="!dirty" @click="reset"><X :size="15" />取消</button><button class="primary" type="button" :disabled="!dirty" @click="save"><Save :size="15" />保存设置</button></footer>
      </main>
    </div>
  </section>
</template>
