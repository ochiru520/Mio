<script>
import { inject } from 'vue'
import {
  Download,
  RefreshCw,
  Sparkles,
  Upload,
  UserRound,
} from '@lucide/vue'
import {
  PRESET_CARDS,
  RELATION_TEMPLATES,
  TONE_TEMPLATES,
  applyProfileFields,
  buildCharacterCard,
  fillMappedIntoDraft,
  findRelationKey,
  findToneKey,
  parseCharacterCard,
  parseCharacterCardFile,
  safeCardFileName,
} from '../characterCard.js'

export default {
  name: 'CharacterCardPanel',
  components: {
    Download,
    RefreshCw,
    Sparkles,
    Upload,
    UserRound,
  },
  setup() {
    const context = inject('mio-settings-page')
    if (!context) throw new Error('设置页上下文未初始化')
    return { context }
  },
  data() {
    return {
      relationOptions: Object.entries(RELATION_TEMPLATES).map(([id, template]) => ({ id, ...template })),
      toneOptions: Object.entries(TONE_TEMPLATES).map(([id, template]) => ({ id, ...template })),
      presetCards: PRESET_CARDS,
      selectedPresetId: '',
      relationKey: '',
      toneKey: '',
      importBusy: false,
      importMessage: '',
      importOk: false,
      exportMessage: '',
    }
  },
  computed: {
    draft() {
      return this.context.mioProfileDraft
    },
    cardReady() {
      return Boolean(this.context.mioProfileReady && this.draft)
    },
    selectedPreset() {
      return this.presetCards.find((card) => card.id === this.selectedPresetId)
    },
  },
  watch: {
    draft(next, previous) {
      if (next === previous) return
      this.importMessage = ''
      this.importOk = false
      this.exportMessage = ''
      this.selectedPresetId = ''
      this.syncTemplateKeys()
    },
  },
  methods: {
    handleRelationChange(event) {
      const key = event.target.value
      this.relationKey = key
      if (key && key !== 'custom' && this.draft) {
        this.draft.preferences.relationship_distance = RELATION_TEMPLATES[key].text
      }
    },
    handleToneChange(event) {
      const key = event.target.value
      this.toneKey = key
      if (key && key !== 'custom' && this.draft) {
        this.draft.speaking_style.tone = TONE_TEMPLATES[key].text
      }
    },
    syncTemplateKeys() {
      if (!this.draft) return
      this.relationKey = findRelationKey(this.draft.preferences.relationship_distance)
      this.toneKey = findToneKey(this.draft.speaking_style.tone)
    },
    applyPreset(card) {
      if (!this.cardReady || !card) return
      const confirmed = window.confirm(`套用「${card.name}」会覆盖当前的一句话人设、语气、关系与称呼（不影响行为与边界），继续吗？`)
      if (!confirmed) return
      applyProfileFields(this.draft, card.profile)
      this.syncProfileTextDrafts()
      this.syncTemplateKeys()
      this.importMessage = ''
    },
    syncProfileTextDrafts() {
      this.context.mioProfileAvoidDraft = (this.draft.speaking_style?.avoid || []).join('\n')
      this.context.mioProfileNotesDraft = (this.draft.preferences?.custom_notes || []).join('\n')
    },
    exportCard() {
      if (!this.cardReady) return
      const card = buildCharacterCard(this.draft)
      const blob = new Blob([JSON.stringify(card, null, 2)], { type: 'application/json;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `角色卡-${safeCardFileName(card.data.name)}.json`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
      this.exportMessage = '已导出角色卡文件'
      setTimeout(() => { this.exportMessage = '' }, 4000)
    },
    handleImport(event) {
      const file = event.target.files && event.target.files[0]
      event.target.value = ''
      if (!file) return
      this.importBusy = true
      parseCharacterCardFile(file)
        .then((result) => {
          if (!result.ok) {
            this.importMessage = result.message
            this.importOk = false
            return
          }
          if (!this.cardReady) {
            this.importMessage = '人格设定尚未就绪，稍后再试'
            this.importOk = false
            return
          }
          fillMappedIntoDraft(this.draft, result.mapped)
          this.context.mioProfileAvoidDraft = (this.draft.speaking_style?.avoid || []).join('\n')
          this.context.mioProfileNotesDraft = (this.draft.preferences?.custom_notes || []).join('\n')
          this.syncTemplateKeys()
          const typeLabel = result.mapped.importKind === 'worldbook' ? 'ST 世界书' : (file.type === 'image/png' || file.name.toLowerCase().endsWith('.png') ? 'PNG 角色卡' : 'JSON 角色卡')
          const ruleLabel = result.mapped.importedRuleCount ? `，包含 ${result.mapped.importedRuleCount} 条世界书规则` : ''
          const importedName = result.mapped.sourceName || result.mapped.name || '未命名角色'
          this.importMessage = `已载入${typeLabel}：${importedName}${ruleLabel}。高级编辑也已同步，检查后点页面底部「保存」生效`
          this.importOk = true
        })
        .catch(() => {
          this.importMessage = '读取文件失败'
          this.importOk = false
        })
        .finally(() => {
          this.importBusy = false
        })
    },
    restoreProfileDefaults() {
      if (!this.cardReady) return
      const confirmed = window.confirm('清空当前卡片内容，恢复到公开版中性的默认角色？此操作只影响正在编辑的内容，未保存前可点「取消」放弃。')
      if (!confirmed) return
      const blank = PRESET_CARDS.find((card) => card.id === 'blank-card')
      if (blank) applyProfileFields(this.draft, blank.profile)
      this.syncProfileTextDrafts()
      this.syncTemplateKeys()
    },
  },
}
</script>

<template>
  <div v-if="cardReady" class="settings-section-block character-card-panel">
    <div class="settings-block-heading">
      <div><h2>角色卡</h2><p>基础设定 + 导入导出 + 高级编辑，三步把角色调成你想要的样子</p></div>
    </div>

    <div class="character-card-section">
      <div class="character-card-section-heading"><span><UserRound :size="14" />基础设定</span><small>最常用的几项</small></div>
      <div class="character-card-grid">
        <label class="profile-wide-field"><span>一句话人设</span><textarea v-model.trim="draft.identity.core" rows="2" maxlength="2000" placeholder="例如：温柔体贴的陪伴型伙伴，记得你的小事" /></label>
        <label class="profile-wide-field"><span>对你的称呼</span><input v-model.trim="draft.preferences.user_address" type="text" maxlength="80" placeholder="例如：你 / 小落" /></label>
        <label class="profile-wide-field"><span>关系与亲密程度</span>
          <select :value="relationKey" @change="handleRelationChange">
            <option value="">选择关系（可再自由编辑）</option>
            <option v-for="option in relationOptions" :key="option.id" :value="option.id">{{ option.label }}</option>
            <option value="custom">自定义</option>
          </select>
          <textarea v-model.trim="draft.preferences.relationship_distance" rows="2" maxlength="2000" />
        </label>
        <label class="profile-wide-field"><span>语气基调</span>
          <select :value="toneKey" @change="handleToneChange">
            <option value="">选择语气（可再自由编辑）</option>
            <option v-for="option in toneOptions" :key="option.id" :value="option.id">{{ option.label }}</option>
            <option value="custom">自定义</option>
          </select>
          <textarea v-model.trim="draft.speaking_style.tone" rows="2" maxlength="3000" />
        </label>
      </div>
    </div>

    <div class="character-card-section">
      <div class="character-card-section-heading"><span><Sparkles :size="14" />角色预设</span><small>选择一个起点，再按你的喜好调整</small></div>
      <div class="character-preset-picker">
        <label class="character-preset-selection">
          <span>选择预设</span>
          <select v-model="selectedPresetId" aria-label="选择角色预设">
            <option value="">选择预设…</option>
            <option v-for="card in presetCards" :key="card.id" :value="card.id">{{ card.name }}</option>
          </select>
        </label>
        <p class="character-preset-description">{{ selectedPreset?.tagline || '温柔陪伴、轻松聊天，或专注做事。' }}</p>
        <button type="button" class="character-preset-apply" :disabled="!selectedPreset" @click="applyPreset(selectedPreset)">套用预设</button>
      </div>
      <div class="character-preset-footer"><span>套用后可继续编辑，保存后生效。</span><button type="button" class="character-preset-clear" @click="restoreProfileDefaults"><RefreshCw :size="13" />恢复中性默认</button></div>
    </div>

    <div class="character-card-section">
      <div class="character-card-section-heading"><span><Upload :size="14" />导入与导出</span><small>使用自己的角色卡</small></div>
      <div class="character-card-exchange-row">
        <label class="character-file-label">
          <Upload :size="14" />导入角色卡
          <input type="file" accept=".png,.json,.st,application/json,image/png" :disabled="importBusy" @change="handleImport" />
        </label>
        <button type="button" class="character-file-button" :disabled="!cardReady" @click="exportCard"><Download :size="14" />导出角色卡</button>
        <span class="character-card-exchange-hint">兼容酒馆 PNG V2/V3、JSON 角色卡与 ST 世界书；系统提示和世界书会进入可见的「行为与边界」，不会授予系统权限</span>
      </div>
      <div v-if="importMessage" :class="['character-card-feedback', { ok: importOk }]">{{ importMessage }}</div>
      <div v-if="exportMessage" class="character-card-feedback ok">{{ exportMessage }}</div>
    </div>

    <p class="settings-note">角色名字和头像在上方「头像与名字」里改；高级内容（表达方式、行为与边界）在下方「高级编辑」里。</p>
  </div>
</template>

<style scoped>
.character-preset-picker { display: grid; grid-template-columns: minmax(150px, 200px) minmax(0, 1fr) auto; align-items: center; gap: 18px; margin-top: 15px; }
.character-preset-selection { display: block; min-width: 0; }
.character-preset-selection > span { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.character-preset-selection select { width: 100%; min-height: 40px; padding: 9px 32px 9px 12px; margin: 0; border: 1px solid var(--line); border-radius: 8px; background: var(--surface-raised); color: var(--ink); font: inherit; font-size: calc(13px * var(--fs, 1)); }
.character-preset-description { min-width: 0; margin: 0; font-size: calc(12px * var(--fs, 1)); line-height: 1.7; color: var(--muted); overflow-wrap: anywhere; }
.character-preset-apply { flex-shrink: 0; min-height: 38px; padding: 8px 14px; border: 1px solid var(--accent); border-radius: 8px; background: var(--accent); color: var(--accent-contrast); font: inherit; font-size: calc(12px * var(--fs, 1)); cursor: pointer; white-space: nowrap; }
.character-preset-apply:disabled { opacity: 1; cursor: default; color: var(--text-muted); background: var(--surface-soft); border-color: var(--border-strong); }
.character-preset-apply:hover:not(:disabled) { filter: brightness(.95); }
.character-preset-footer { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; margin-top: 12px; color: var(--muted); font-size: calc(11px * var(--fs, 1)); line-height: 1.7; }
.character-preset-clear { display: inline-flex; align-items: center; gap: 5px; padding: 4px 0; border: 0; background: transparent; color: var(--muted); font: inherit; cursor: pointer; }
.character-preset-clear:hover { color: var(--accent-dark); }
.character-preset-picker select:focus-visible, .character-preset-apply:focus-visible, .character-preset-clear:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
@media (max-width: 700px) {
  .character-preset-picker { grid-template-columns: minmax(0, 1fr) auto; gap: 10px 12px; }
  .character-preset-description { grid-column: 1 / -1; grid-row: 2; }
  .character-preset-footer { align-items: flex-start; }
}
</style>
