<script setup>
import { computed } from 'vue'
import { Check, Gamepad2, ImagePlus, Plus, Play, Power, RotateCw, Trash2, Volume2 } from '@lucide/vue'

const props = defineProps({
  status: { type: Object, required: true },
  avatarUrl: { type: String, required: true },
  busy: { type: Boolean, default: false },
  controlMode: { type: Boolean, default: false },
  managementMode: { type: Boolean, default: false },
})
const emit = defineEmits([
  'control',
  'delete-live2d',
  'import-live2d',
  'preview-expression',
  'preview-motion',
  'replace-live2d-preview',
  'manage',
  'save',
  'save-size',
])
const pet = computed(() => props.status.pet || { settings: {} })
const settings = computed(() => pet.value.settings || {})
const live2d = computed(() => pet.value.live2d || {})
const appAssetBase = window.location.pathname.startsWith('/agent-app') ? '/agent-app' : ''
const appearanceItems = computed(() => [
  ...(live2d.value.models || []).map((model) => ({
    id: model.id,
    name: model.name,
    type: 'Live2D',
    preview: model.preview_url
      ? `${model.preview_url}?v=${encodeURIComponent(model.id)}`
      : model.preview_path
        ? `${appAssetBase}/live2d-pet/${model.preview_path}`
        : props.avatarUrl,
    imported: Boolean(model.imported),
    capabilities: model.capabilities || {},
  })),
])
const selectedLive2D = computed(() => {
  if (settings.value.pet_renderer !== 'live2d') return null
  return appearanceItems.value.find((item) => item.id === settings.value.live2d_model_id) || null
})
const motionLabels = {
  idle: '待机', touch: '触碰', think: '思考', speak: '说话', observe: '观察',
  cheerful: '开心', concerned: '担心', alert: '警觉', attention: '注意', shy: '害羞',
}
const expressionLabels = {
  neutral: '自然', gentle: '温柔', cheerful: '开心', concerned: '担心', serious: '认真', shy: '害羞',
}
const selectedMotions = computed(() => selectedLive2D.value?.capabilities?.motions || [])
const selectedExpressions = computed(() => selectedLive2D.value?.capabilities?.expressions || [])
const currentMotionSlots = computed(() => {
  if (!selectedLive2D.value) return {}
  settings.value.live2d_motion_slots ||= {}
  settings.value.live2d_motion_slots[selectedLive2D.value.id] ||= {}
  return settings.value.live2d_motion_slots[selectedLive2D.value.id]
})
const currentExpressionSlots = computed(() => {
  if (!selectedLive2D.value) return {}
  settings.value.live2d_expression_slots ||= {}
  settings.value.live2d_expression_slots[selectedLive2D.value.id] ||= {}
  return settings.value.live2d_expression_slots[selectedLive2D.value.id]
})

function isSelected(item) {
  return settings.value.pet_renderer === 'live2d' && settings.value.live2d_model_id === item.id
}

function selectAppearance(item) {
  settings.value.pet_renderer = 'live2d'
  settings.value.live2d_model_id = item.id
}

function rangeInputStyle(value, minimum, maximum) {
  const min = Number(minimum)
  const max = Number(maximum)
  const current = Number(value)
  const percent = max > min ? Math.max(0, Math.min(100, ((current - min) / (max - min)) * 100)) : 0
  return { '--range-progress': `${percent}%` }
}
</script>

<template>
  <section class="pet-appearance-panel simplified-pet-panel">
    <header class="pet-appearance-heading"><div><Gamepad2 :size="18" /><strong>桌宠</strong></div><span :class="['connection-label', { online: pet.running }]">{{ pet.running ? '运行中' : '未启动' }}</span></header>
    <div class="appearance-unified-list">
      <div v-for="item in appearanceItems" :key="item.id" class="appearance-item-wrap">
        <button type="button" :class="{ active: isSelected(item) }" @click="selectAppearance(item)"><img :src="item.preview" :alt="item.name" /><span><strong>{{ item.name }}</strong><small>{{ item.type }}</small></span><Check v-if="isSelected(item)" class="appearance-selected-mark" :size="15" /></button>
        <span v-if="managementMode && item.imported" class="appearance-card-actions"><label :title="`更换 ${item.name} 的封面`"><input type="file" accept="image/*" @change="emit('replace-live2d-preview', item, $event)" /><ImagePlus :size="14" /></label><button class="appearance-delete" type="button" :title="`删除 ${item.name}`" :disabled="busy" @click.stop="emit('delete-live2d', item)"><Trash2 :size="14" /></button></span>
      </div>
      <template v-if="managementMode">
        <button class="appearance-add" type="button" :disabled="busy" @click="emit('import-live2d')"><Plus :size="19" /><span><strong>导入 Live2D</strong><small>选择包含 model3.json 的目录</small></span></button>
      </template>
      <button v-else class="appearance-add" type="button" @click="emit('manage')"><Plus :size="19" /><span><strong>添加形象</strong><small>前往形象管理</small></span></button>
    </div>
    <div v-if="managementMode && selectedLive2D" class="live2d-resource-manager">
      <header><strong>{{ selectedLive2D.name }} 的动作与表情</strong><small>说话时会按语义和情绪自动使用；也可以手动指定</small></header>
      <div v-if="selectedMotions.length" class="live2d-resource-section"><h3>动作</h3><div class="live2d-resource-grid"><label v-for="(label, slot) in motionLabels" :key="slot"><span>{{ label }}</span><select v-model="currentMotionSlots[slot]"><option value="">自动判断</option><option v-for="motion in selectedMotions" :key="motion.name" :value="motion.name">{{ motion.name }}（{{ motion.count || 0 }}）</option></select><button type="button" title="预览动作" :disabled="!currentMotionSlots[slot] || busy" @click="emit('preview-motion', currentMotionSlots[slot])"><Play :size="13" /></button></label></div></div>
      <div v-if="selectedExpressions.length" class="live2d-resource-section"><h3>表情</h3><div class="live2d-resource-grid"><label v-for="(label, slot) in expressionLabels" :key="slot"><span>{{ label }}</span><select v-model="currentExpressionSlots[slot]"><option value="">自动判断</option><option v-for="expression in selectedExpressions" :key="expression.Name || expression.name" :value="expression.Name || expression.name">{{ expression.Name || expression.name }}</option></select><button type="button" title="预览表情" :disabled="!currentExpressionSlots[slot] || busy" @click="emit('preview-expression', currentExpressionSlots[slot])"><Volume2 :size="13" /></button></label></div></div>
      <p v-if="!selectedMotions.length && !selectedExpressions.length">模型配置中没有登记动作或表情</p>
    </div>
    <div class="pet-quick-controls">
      <div class="settings-row settings-range-row"><div><strong>桌宠大小</strong><small>保存后同步到桌面</small></div><input v-model.number="settings.pet_size_percent" type="range" min="80" max="240" step="1" :style="rangeInputStyle(settings.pet_size_percent, 80, 240)" @change="emit('save-size')" /><b>{{ settings.pet_size_percent }}%</b></div>
      <div class="pet-toggle-grid"><label><span>固定并穿透</span><span class="switch-control"><input v-model="settings.live2d_click_through_locked" type="checkbox" /><i /></span></label><label><span>始终置顶</span><span class="switch-control"><input v-model="settings.live2d_always_on_top" type="checkbox" /><i /></span></label><label><span>说话气泡</span><span class="switch-control"><input v-model="settings.live2d_speech_bubble_enabled" type="checkbox" /><i /></span></label></div>
    </div>
    <div class="pet-runtime-actions">
      <button class="primary-button" type="button" :disabled="busy" @click="emit('control', 'start')"><Play :size="15" />启动</button>
      <button type="button" :disabled="busy" @click="emit('control', 'restart')"><RotateCw :size="15" />重启</button>
      <button type="button" :disabled="busy" @click="emit('control', 'stop')"><Power :size="15" />停止</button>
      <button type="button" :disabled="busy" @click="emit('save')"><Check :size="15" />应用修改</button>
    </div>
  </section>
</template>
