<script setup>
import { computed, ref, watch } from 'vue'
const props = defineProps({ preview: { type: Object, required: true } })
const sheetIndex = ref(0)
watch(() => props.preview, () => { sheetIndex.value = 0 })
const sheet = computed(() => props.preview.sheets?.[sheetIndex.value])
const pages = computed(() => props.preview.format === 'pptx' ? (props.preview.slides || []) : [{ blocks: props.preview.blocks || [] }])
function imageUrl(value) {
  return /^data:image\/(png|jpeg|webp|gif);base64,[a-z\d+/=\s]+$/i.test(value || '') ? value : ''
}
</script>

<template>
  <article class="agent-office-preview" aria-label="文件内容预览">
    <p v-if="preview.notice" class="agent-office-notice">{{ preview.notice }}</p>
    <template v-if="preview.format === 'xlsx'">
      <nav v-if="preview.sheets?.length" class="agent-sheet-tabs" aria-label="工作表">
        <button v-for="(item,index) in preview.sheets" :key="index" type="button" :aria-pressed="sheetIndex === index" @click="sheetIndex = index">{{ item.name }}</button>
      </nav>
      <section v-if="sheet" :aria-label="sheet.name">
        <div class="agent-office-table-scroll"><table><tbody><tr v-for="(row,index) in sheet.rows" :key="index"><th scope="row">{{ sheet.row_numbers?.[index] || index + 1 }}</th><td v-for="(cell,column) in row" :key="column">{{ cell }}</td></tr></tbody></table></div>
        <p v-if="sheet.truncated" class="agent-office-notice">此工作表内容较多，当前展示部分行列。</p>
        <p v-if="!sheet.rows?.length" class="agent-office-notice">此工作表没有可显示的内容。</p>
      </section>
    </template>
    <template v-else>
      <section v-for="(page,index) in pages" :key="index" :class="{ 'agent-slide-preview': preview.format === 'pptx' }">
        <small v-if="preview.format === 'pptx'" class="agent-slide-number">第 {{ page.number || index + 1 }} 张幻灯片</small>
        <template v-for="(block,blockIndex) in page.blocks" :key="blockIndex">
          <component v-if="block.type === 'heading'" :is="`h${Math.min(6, Math.max(1, Number(block.level) || 2))}`">{{ block.text }}</component>
          <p v-else-if="block.type === 'paragraph'">{{ block.text }}</p>
          <div v-else-if="block.type === 'table'" class="agent-office-table-scroll"><table><tbody><tr v-for="(row,rowIndex) in block.rows" :key="rowIndex"><td v-for="(cell,column) in row" :key="column">{{ cell }}</td></tr></tbody></table></div>
          <figure v-else-if="block.type === 'image' && imageUrl(block.url)"><img :src="imageUrl(block.url)" :alt="block.alt || '文档图片'" loading="lazy" /></figure>
        </template>
      </section>
      <p v-if="!pages.length || pages.every(page => !page.blocks?.length)" class="agent-office-notice">此文件没有可显示的文字或图片。</p>
    </template>
  </article>
</template>

<style scoped>
.agent-office-preview { margin: 18px 0; color: var(--text); font-size: calc(13px * var(--fs, 1)); line-height: 1.85; overflow-wrap: anywhere; }
.agent-office-preview p { white-space: pre-wrap; margin: 0 0 16px; }
.agent-office-preview h1 { font-size: 1.5em; }
.agent-office-preview h2 { font-size: 1.25em; }
.agent-office-preview h3, .agent-office-preview h4, .agent-office-preview h5, .agent-office-preview h6 { font-size: 1.1em; }
.agent-office-preview :is(h1,h2,h3,h4,h5,h6) { margin: 24px 0 12px; line-height: 1.5; font-weight: 600; }
.agent-office-notice { color: var(--text-muted); font-size: calc(12px * var(--fs, 1)); line-height: 1.65; }
.agent-office-table-scroll { overflow-x: auto; margin: 14px 0 20px; max-width: 100%; }
.agent-office-preview table { border-collapse: collapse; width: max-content; min-width: 100%; font-size: inherit; }
.agent-office-preview td, .agent-office-preview th { border: 1px solid var(--border); padding: 8px 10px; text-align: left; vertical-align: top; white-space: pre-wrap; min-width: 70px; max-width: 320px; }
.agent-office-preview th { min-width: 32px; color: var(--text-muted); font-weight: 400; background: var(--surface-soft); }
.agent-office-preview figure { margin: 18px 0; }
.agent-office-preview img { display: block; width: auto; max-width: 100%; max-height: 480px; height: auto; object-fit: contain; border-radius: 5px; }
.agent-sheet-tabs { display: flex; overflow-x: auto; gap: 6px; margin-bottom: 16px; }
.agent-sheet-tabs button { flex-shrink: 0; padding: 6px 10px; background: transparent; border: 1px solid var(--border); border-radius: 6px; color: inherit; font: inherit; cursor: pointer; }
.agent-sheet-tabs button[aria-pressed=true] { background: var(--surface-soft); color: var(--accent); }
.agent-slide-preview + .agent-slide-preview { margin-top: 24px; padding-top: 20px; border-top: 1px solid var(--border); }
.agent-slide-number { color: var(--text-muted); }
</style>
