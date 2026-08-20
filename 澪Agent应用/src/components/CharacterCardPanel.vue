<script>
import { inject } from 'vue'
import {
  Download,
  Heart,
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
    Heart,
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
  },
  watch: {
    draft(next, previous) {
      if (next === previous) return
      this.importMessage = ''
      this.importOk = false
      this.exportMessage = ''
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
      if (!this.cardReady) return
      const confirmed = window.confirm(`套用「${card.name}」会覆盖当前的一句话人设、语气、关系与称呼（不影响行为与边界），继续吗？`)
      if (!confirmed) return
      applyProfileFields(this.draft, card.profile)
      this.syncTemplateKeys()
      this.importMessage = ''
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
      <div class="character-card-section-heading"><span><Upload :size="14" />导入与导出</span><small>一键套预设，或直接交换别人做好的角色卡</small></div>
      <div class="character-preset-row">
        <span class="character-preset-label"><Sparkles :size="14" />一键套用</span>
        <button v-for="card in presetCards" :key="card.id" type="button" class="character-preset-button" @click="applyPreset(card)">
          <Heart :size="14" /><span>{{ card.name }}</span><small>{{ card.tagline }}</small>
        </button>
        <button type="button" class="character-preset-button character-preset-reset" @click="restoreProfileDefaults"><RefreshCw :size="14" /><span>清空为中性默认</span></button>
      </div>
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
