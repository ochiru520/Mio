<script setup>
import appMetadata from '../../package.json' with { type: 'json' }
import { computed, inject, ref, watch, onBeforeUnmount, nextTick } from 'vue'
import { ArrowLeft, Search, Code2, Gauge, FolderKey, Workflow, Wrench, Image, ShieldCheck, BarChart3, BookOpen, Globe, Sparkles, Brain, ListChecks } from '@lucide/vue'
import SettingsPage from './SettingsPage.vue'
import AgentTaskSettings from './AgentTaskSettings.vue'
import AgentCreationSettings from './AgentCreationSettings.vue'
import ExecutionSettings from './ExecutionSettings.vue'
import AgentAppearancePanel from './AgentAppearancePanel.vue'
import AgentToolSettings from './AgentToolSettings.vue'
import AgentProjectSettings from './AgentProjectSettings.vue'
import AgentSkills from './AgentSkills.vue'
import AgentCapabilities from './AgentCapabilities.vue'
import SelfUpgradePanel from './SelfUpgradePanel.vue'
import MioUsageSettings from './MioUsageSettings.vue'
import MioMemorySettings from './MioMemorySettings.vue'
const props = defineProps({ agent: Boolean, projectId: String, conversationId: String, projects: { type: Array, default: () => [] } })
const emit = defineEmits(['return'])
const settings = inject('mio-settings-page')
const chat = inject('mio-chat-page')
const developmentConversationId = computed(() => {
  const id = props.conversationId || chat?.selectedConversationId || ''
  return chat?.conversations?.some(item => item.id === id && item.kind === 'agent') ? id : ''
})
async function openDevelopmentConversation(id) {
  if (!id || !chat) return
  if (!chat.conversations?.some(item => item.id === id && item.kind === 'agent')) {
    const { apiRequest } = await import('../services/api.js')
    chat.conversations = (await apiRequest('/api/agent/conversations')).filter(item => item.kind === 'agent')
  }
  if (!chat.conversations?.some(item => item.id === id && item.kind === 'agent')) throw new Error('开发对话不存在，请重新读取任务。')
  await chat.selectConversation(id)
  chat.agentSurfaceMode = 'work'
  chat.activeView = 'chat'
  window.dispatchEvent(new Event('mio-agent-workspace-changed'))
  emit('return')
}
const search = ref(''), content = ref(null)
const extras = [{ id: 'tools', label: '工具与权限', icon: Wrench }, { id: 'autonomy', label: '自主策略', icon: ShieldCheck }, { id: 'receipts', label: '执行记录', icon: ListChecks }, { id: 'budget', label: '任务执行', icon: Gauge }, { id: 'execution', label: '执行权限', icon: Code2 }, { id: 'self-upgrade', label: '改造 Mio', icon: Code2 },
  { id: 'files', label: '文件权限', icon: FolderKey }, { id: 'creation', label: '创作环境', icon: Wrench }, { id: 'workflows', label: '工作流', icon: Workflow }, { id: 'knowledge', label: '知识库', icon: BookOpen }, { id: 'skills', label: '能力', icon: Sparkles }, { id: 'browser', label: '浏览器', icon: Globe }, { id: 'agent-images', label: 'Agent 图片', icon: Image }]
const sharedIds = ['appearance', 'profile', 'models']
const commonExtras = [{ id: 'usage', label: '模型用量', icon: BarChart3 }, { id: 'memory-settings', label: '记忆', icon: Brain }]
const filteredCommon = computed(() => {
  const query = search.value.trim().toLowerCase()
  return commonExtras.filter(item => !query || item.label.toLowerCase().includes(query))
})
const extra = computed(() => commonExtras.some(item => item.id === settings.activeSettingsSection) || props.agent && extras.some(item => item.id === settings.activeSettingsSection) ? settings.activeSettingsSection : '')
const allowedSections = computed(() => props.agent
  ? [...sharedIds, ...extras.map(item => item.id), ...commonExtras.map(item => item.id)]
  : [...settings.settingsNavigation.flatMap(group => group.items.map(item => item.id)), ...commonExtras.map(item => item.id)])
watch(search, value => { settings.settingsSearch = value }, { immediate: true })
onBeforeUnmount(() => { settings.settingsSearch = '' })
const groups = computed(() => settings.settingsNavigation.map(group => ({
  ...group, label: props.agent ? '共用功能' : '聊天设置',
  items: group.items.filter(item => !props.agent || sharedIds.includes(item.id)).map(item => props.agent ? {
    ...item, label: ({ appearance: '外观主题', profile: '人设与表达' })[item.id] || item.label,
  } : item),
})).map(group => ({ ...group, items: group.items.filter(item => `${item.label} ${item.description} ${item.keywords}`.toLowerCase().includes(search.value.trim().toLowerCase())) })).filter(group => group.items.length))
const primaryIds = ['execution', 'budget', 'skills', 'self-upgrade']
const filteredExtras = computed(() => extras.filter(item => primaryIds.includes(item.id) && item.label.toLowerCase().includes(search.value.trim().toLowerCase())))
const advancedExtras = computed(() => extras.filter(item => !primaryIds.includes(item.id) && item.label.toLowerCase().includes(search.value.trim().toLowerCase())))
async function select(id) { await settings.openSettingsSection(id) }
watch(() => settings.activeSettingsSection, async () => { await nextTick(); if (content.value) content.value.scrollTop = 0 })
watch(
  () => [props.agent, settings.activeSettingsSection, allowedSections.value.join('|')],
  () => {
    if (!allowedSections.value.includes(settings.activeSettingsSection)) {
      settings.openSettingsSection(props.agent ? 'budget' : 'general')
    }
  },
  { immediate: true },
)
</script>
<template>
  <section class="mio-settings-window settings-window-shell embedded-settings-shell agent-settings-shell">
    <aside class="mio-settings-sidebar" tabindex="0" aria-label="设置导航">
      <div class="mio-settings-identity"><img :src="chat.avatarUrl" :alt="chat.mioDisplayName" /><span>{{ props.agent ? 'Mio Agent 设置' : 'Mio 聊天设置' }}</span></div>
      <button type="button" class="mio-settings-return" @click="emit('return')"><ArrowLeft :size="16" />返回应用</button>
      <label class="mio-settings-search"><Search :size="15" /><input v-model="search" type="search" aria-label="搜索设置" placeholder="搜索设置" /></label>
      <nav aria-label="设置分类">
        <template v-if="props.agent && filteredExtras.length"><small>Agent 设置</small><button v-for="item in filteredExtras" :key="item.id" type="button" :class="{ selected: extra === item.id }" @click="select(item.id)"><component :is="item.icon" :size="16" />{{ item.label }}</button></template>
        <details v-if="props.agent && advancedExtras.length" class="advanced-navigation"><summary>高级设置</summary><button v-for="item in advancedExtras" :key="item.id" type="button" :class="{ selected: extra === item.id }" @click="select(item.id)"><component :is="item.icon" :size="16" />{{ item.label }}</button></details>
        <template v-for="group in groups" :key="group.label"><small>{{ group.label }}</small><button v-for="item in group.items" :key="item.id" type="button" :class="{ selected: !extra && settings.activeSettingsSection === item.id }" @click="select(item.id)"><component :is="item.icon" :size="16" />{{ item.label }}<i v-if="settings.isSettingsSectionDirty(item.id)" /></button></template>
        <template v-if="filteredCommon.length"><small>共用数据</small><button v-for="item in filteredCommon" :key="item.id" type="button" :class="{ selected: extra === item.id }" @click="select(item.id)"><component :is="item.icon" :size="16" />{{ item.label }}</button></template>
      </nav>
      <small class="mio-settings-version">Mio {{ settings.runtimeIdentity?.app_version || settings.runtimeIdentity?.version || settings.runtimeIdentity?.build?.version || appMetadata.version }}</small>
    </aside>
    <main ref="content" class="mio-settings-content" tabindex="0" aria-label="设置内容">
      <AgentToolSettings v-if="extra === 'tools' || extra === 'autonomy' || extra === 'receipts'" :initial-tab="extra" />
      <ExecutionSettings :initial-project-id="projectId" v-else-if="extra === 'execution'" :projects="props.projects" />
      <AgentTaskSettings v-else-if="extra === 'budget' || extra === 'files'" :section="extra" />
      <section v-else-if="extra === 'creation' || extra === 'workflows'" class="mio-settings-extra"><header class="agent-setting-title"><h1>{{ extra === 'creation' ? '创作环境' : '工作流' }}</h1><p>使用你已连接的本机创作能力</p></header><AgentCreationSettings :section="extra" /></section>
      <AgentProjectSettings v-else-if="extra === 'knowledge' || extra === 'browser'" :key="extra" :section="extra" :initial-project-id="projectId" :projects="props.projects" />
      <AgentCapabilities v-else-if="extra === 'skills'" /><section v-else-if="extra === 'self-upgrade'" class="mio-settings-extra"><SelfUpgradePanel :conversation-id="developmentConversationId" :open-conversation="openDevelopmentConversation" /></section>
      <MioUsageSettings v-else-if="extra === 'usage'" />
      <MioMemorySettings v-else-if="extra === 'memory-settings'" />
      <AgentAppearancePanel v-else-if="extra === 'agent-images'" />
      <SettingsPage v-else :agent="props.agent" />
    </main>
  </section>
</template>
