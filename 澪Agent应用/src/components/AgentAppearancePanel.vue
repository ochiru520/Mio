<script setup>
import { computed, inject, onMounted, ref } from 'vue'
import { apiRequest } from '../services/api.js'

const context = inject('mio-chat-page', {})
const labels = { hero: '首页问候图片', personal: '我的空间图片' }
const data = ref({ hero_shape: 'original' }), busy = ref(true), ready = ref(false), error = ref(''), notice = ref('')
const savedShape = ref('original'), imageNonce = ref(Date.now()), previewErrors = ref({})
const images = computed(() => {
  const avatar = context.avatarUrl || '/api/settings/avatar'
  const defaults = { hero: avatar, personal: context.userAvatarCustom ? context.userAvatarUrl : avatar }
  return Object.entries(labels).map(([kind, label]) => {
    const customUrl = data.value[`${kind}_url`]
    return { kind, label, custom: Boolean(customUrl), url: customUrl ? `${customUrl}${customUrl.includes('?') ? '&' : '?'}v=${imageNonce.value}` : defaults[kind] }
  })
})

function applyAppearance(value) {
  data.value = { hero_shape: 'original', ...value }
  savedShape.value = data.value.hero_shape
  imageNonce.value = Math.max(Date.now(), imageNonce.value + 1)
  previewErrors.value = {}
}
async function load() {
  busy.value = true; error.value = ''; notice.value = ''
  try { applyAppearance(await apiRequest('/api/agent/surface')); ready.value = true }
  catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}
function changed() {
  try { localStorage.setItem('mio_agent_appearance_updated', String(imageNonce.value)) } catch { /* A restricted storage context must not hide a successful save. */ }
  window.dispatchEvent(new Event('mio-agent-appearance'))
}
function retryPreview(kind) {
  previewErrors.value = { ...previewErrors.value, [kind]: '' }
  imageNonce.value = Math.max(Date.now(), imageNonce.value + 1)
}
async function upload(event, kind) {
  const file = event.target.files?.[0]; event.target.value = ''; if (!file) return
  error.value = ''; notice.value = ''
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) { error.value = '请选择 PNG、JPEG 或 WebP 图片。'; return }
  if (file.size > 8 * 1024 * 1024) { error.value = '图片不能超过 8 MB。'; return }
  busy.value = true
  try {
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result)
      reader.onerror = () => reject(new Error('无法读取图片，请重新选择。'))
      reader.readAsDataURL(file)
    })
    applyAppearance(await apiRequest(`/api/agent/surface/images/${kind}`, { method: 'POST', body: JSON.stringify({ data_url: dataUrl }) }))
    notice.value = `${labels[kind]}已更新。`; changed()
  } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}
async function reset(kind) {
  busy.value = true; error.value = ''; notice.value = ''
  try {
    applyAppearance(await apiRequest(`/api/agent/surface/images/${kind}`, { method: 'DELETE' }))
    notice.value = `${labels[kind]}已恢复默认。`; changed()
  } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}
async function shape() {
  busy.value = true; error.value = ''; notice.value = ''
  try {
    applyAppearance(await apiRequest('/api/agent/surface', { method: 'PUT', body: JSON.stringify({ hero_shape: data.value.hero_shape }) }))
    notice.value = '首页图片形状已更新。'; changed()
  } catch (cause) { data.value.hero_shape = savedShape.value; error.value = cause.message }
  finally { busy.value = false }
}
onMounted(load)
</script>

<template>
  <section class="agent-appearance-settings" :aria-busy="busy">
    <header class="agent-setting-title"><h2>Agent 图片</h2><p>查看当前使用的图片，更换后即时生效。支持 PNG、JPEG、WebP，最大 8 MB。</p></header>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="notice" class="appearance-notice" role="status">{{ notice }}</p>
    <button v-if="!ready" type="button" :disabled="busy" @click="load">{{ busy ? '正在读取图片设置' : '重新读取图片设置' }}</button>
    <article v-for="image in images" :key="image.kind" class="appearance-image-row" :aria-label="image.label">
      <figure class="appearance-preview">
        <div class="appearance-preview-stage" :class="image.kind">
          <img v-if="ready && image.url && previewErrors[image.kind] !== image.url" :key="image.url" :src="image.url" :alt="`${image.label}当前预览`" :class="{ circle: image.kind === 'personal' || data.hero_shape === 'circle' }" @error="previewErrors[image.kind] = image.url" />
          <span v-else class="appearance-preview-unavailable">{{ ready ? '图片暂时无法显示' : '正在读取图片' }}</span>
        </div>
        <figcaption>{{ image.kind === 'hero' ? '首页显示预览' : '当前预览' }}</figcaption>
      </figure>
      <div class="appearance-image-controls">
        <h3>{{ image.label }}</h3>
        <p>{{ !ready ? '正在读取当前设置' : image.custom ? '正在使用自定义图片' : image.kind === 'personal' && context.userAvatarCustom ? '默认图片 · 跟随用户头像' : '默认图片 · 跟随 Mio 头像' }}</p>
        <label class="appearance-upload-label">更换图片<input type="file" :aria-label="`更换${image.label}`" accept="image/png,image/jpeg,image/webp" :disabled="busy || !ready" @change="upload($event, image.kind)" /></label>
        <div class="appearance-image-actions">
          <button type="button" :disabled="busy || !ready || !image.custom" @click="reset(image.kind)">恢复默认</button>
          <button v-if="ready && previewErrors[image.kind] === image.url" type="button" :disabled="busy" @click="retryPreview(image.kind)">重试预览</button>
        </div>
        <label v-if="image.kind === 'hero'" class="appearance-shape">首页图片形状<select aria-label="首页图片形状" v-model="data.hero_shape" :disabled="busy || !ready" @change="shape"><option value="original">保留原图比例</option><option value="circle">圆形</option></select></label>
      </div>
    </article>
  </section>
</template>

<style scoped>
.agent-appearance-settings h2 { margin-bottom: 8px; }
.appearance-image-row { display: grid; grid-template-columns: 180px minmax(0, 1fr); gap: 24px; padding: 24px 0; border-bottom: 1px solid var(--border); }
.appearance-preview { margin: 0; min-width: 0; }
.appearance-preview-stage { display: flex; align-items: center; justify-content: center; height: 200px; padding: 8px; background: var(--surface-soft); border: 1px solid var(--border); border-radius: 10px; }
.appearance-preview-stage img { display: block; max-width: 100%; max-height: 144px; width: auto; height: auto; object-fit: contain; border-radius: 6px; }
.appearance-preview-stage.hero img { max-width: min(100%, var(--agent-hero-max-width, 160px)); max-height: var(--agent-hero-max-height, 180px); }
.appearance-preview-stage img.circle { width: 116px; height: 116px; border-radius: 50%; object-fit: cover; }
.appearance-preview-stage.hero img.circle { width: var(--agent-hero-circle-size, 100px); height: var(--agent-hero-circle-size, 100px); }
.appearance-preview figcaption { margin-top: 8px; text-align: center; font-size: calc(12px * var(--fs, 1)); color: var(--text-muted); }
.appearance-preview-unavailable { color: var(--text-muted); text-align: center; font-size: calc(12px * var(--fs, 1)); }
.appearance-image-controls { min-width: 0; }
.appearance-image-controls h3 { margin: 0 0 6px; font-size: calc(15px * var(--fs, 1)); font-weight: 500; }
.appearance-image-controls p { margin: 0 0 16px; color: var(--text-muted); font-size: calc(12px * var(--fs, 1)); }
.appearance-upload-label, .appearance-shape { font-size: calc(12px * var(--fs, 1)); color: var(--text); }
.appearance-upload-label input { min-width: 0; }
.appearance-image-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
.appearance-shape { margin-top: 18px; max-width: 220px; }
.appearance-shape select { min-width: 0; padding: 8px; color: var(--text); border: 1px solid var(--border-strong); border-radius: 6px; background: var(--surface-raised); font: inherit; }
.appearance-notice { color: var(--text-muted); font-size: calc(12px * var(--fs, 1)); }
@media (max-width: 650px) {
  .mio-settings-content > .agent-appearance-settings { padding: 24px 12px; }
  .appearance-image-row { grid-template-columns: minmax(0, 1fr); gap: 16px; }
  .appearance-preview { width: min(180px, 100%); }
  .appearance-preview-stage.hero { height: 180px; }
}
</style>
