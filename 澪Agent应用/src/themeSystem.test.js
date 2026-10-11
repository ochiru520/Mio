import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

import { DEFAULT_THEME_ID, THEME_GROUPS, THEME_OPTIONS, normalizeThemeId } from './themeOptions.js'

const tokens = fs.readFileSync(new URL('./styles/tokens.css', import.meta.url), 'utf8')

/** 取出 [data-theme="<id>"] 块里的声明文本。 */
function themeBlock(id) {
  const match = tokens.match(new RegExp(`\\[data-theme="${id}"\\]\\s*\\{([\\s\\S]*?)\\n\\}`))
  return match ? match[1] : ''
}

/** 一个主题必须定义的全部语义令牌。缺任何一个，换主题时就会有地方掉回默认值。 */
const REQUIRED_TOKENS = [
  '--bg',
  '--bg-elevated',
  '--surface',
  '--surface-raised',
  '--surface-soft',
  '--surface-sunken',
  '--text',
  '--text-muted',
  '--text-faint',
  '--text-inverse',
  '--border',
  '--border-strong',
  '--accent',
  '--accent-hover',
  '--accent-soft',
  '--accent-contrast',
  '--success',
  '--success-soft',
  '--warning',
  '--warning-soft',
  '--danger',
  '--danger-soft',
  '--info',
  '--info-soft',
  '--shadow-tint',
  '--shadow-strong',
  '--scrollbar-thumb',
  '--sidebar-bg',
  '--sidebar-active',
  '--titlebar-bg',
]

test('每个主题都定义了完整的语义令牌', () => {
  for (const theme of THEME_OPTIONS) {
    const block = themeBlock(theme.id)
    assert.ok(block, `主题 ${theme.id} 应当在 tokens.css 里有一个 [data-theme] 块`)
    const missing = REQUIRED_TOKENS.filter((token) => !new RegExp(`${token}\\s*:`).test(block))
    assert.deepEqual(missing, [], `主题 ${theme.id} 缺少令牌: ${missing.join(', ')}`)
  }
})

test('主题分为浅色与深色两组，且每组都有内容', () => {
  assert.equal(THEME_GROUPS.length, 2)
  const [light, dark] = THEME_GROUPS
  assert.equal(light.label, '浅色')
  assert.equal(dark.label, '深色')
  assert.ok(light.items.length >= 1, '浅色分组不应为空')
  assert.ok(dark.items.length >= 1, '深色分组不应为空')
  // 分组加起来必须覆盖全部主题，不能有主题在界面上选不到
  assert.equal(light.items.length + dark.items.length, THEME_OPTIONS.length)
})

test('深色主题声明 color-scheme: dark，浅色声明 light', () => {
  for (const theme of THEME_OPTIONS) {
    const block = themeBlock(theme.id)
    const expected = theme.mode === 'dark' ? 'dark' : 'light'
    assert.match(
      block,
      new RegExp(`color-scheme:\\s*${expected}`),
      `主题 ${theme.id} 的 color-scheme 应为 ${expected}`,
    )
  }
})

test('默认主题存在且是最先列出的浅色主题', () => {
  const ids = THEME_OPTIONS.map((item) => item.id)
  assert.ok(ids.includes(DEFAULT_THEME_ID), '默认主题必须在主题清单里')
  assert.equal(THEME_OPTIONS[0].id, DEFAULT_THEME_ID)
  assert.equal(THEME_OPTIONS[0].mode, 'light')
})

test('旧主题 id 会映射到新主题，而不是悄悄回退成默认值', () => {
  // 老用户 localStorage 里存的是这五个 id，必须平滑迁移
  assert.equal(normalizeThemeId('clear'), 'celadon')
  assert.equal(normalizeThemeId('forest'), 'bamboo')
  assert.equal(normalizeThemeId('sakura'), 'rose')
  // 仍然存在的 id 保持不变
  assert.equal(normalizeThemeId('mist'), 'mist')
  assert.equal(normalizeThemeId('rose'), 'rose')
  // 无法识别时才回退
  assert.equal(normalizeThemeId('nonexistent'), DEFAULT_THEME_ID)
  assert.equal(normalizeThemeId(undefined), DEFAULT_THEME_ID)
  assert.equal(normalizeThemeId(''), DEFAULT_THEME_ID)
})

test('主题 id 唯一，且每个主题都有可预览的三色样本', () => {
  const ids = THEME_OPTIONS.map((item) => item.id)
  assert.equal(new Set(ids).size, ids.length, '主题 id 不能重复')
  for (const theme of THEME_OPTIONS) {
    assert.equal(theme.swatch.length, 3, `主题 ${theme.id} 的样本条应当是 3 个颜色`)
    for (const color of theme.swatch) {
      assert.match(color, /^#[0-9a-fA-F]{6}$/, `主题 ${theme.id} 的样本色必须是十六进制`)
    }
    assert.ok(theme.label && theme.description, `主题 ${theme.id} 需要名称和描述`)
  }
})

test('令牌层是唯一的颜色来源，其它样式表不再写死颜色', () => {
  const styleFiles = [
    'styles.css',
    'styles/base.css',
    'styles/integrated.css',
    'styles/refinements.css',
    'styles/chat.css',
    'styles/home.css',
    'styles/records.css',
    'styles/settings.css',
    'styles/companion.css',
    'styles/creation.css',
    'styles/agent-workspace.css',
    'styles/agent-settings.css',
  ]

  // var(--x, #fallback) 里的兜底值不算，那是有意为之的降级
  const stripFallbacks = (source) => source.replace(/var\(\s*--[\w-]+\s*,[^()]*\)/g, '')

  for (const file of styleFiles) {
    const source = stripFallbacks(fs.readFileSync(new URL(`./${file}`, import.meta.url), 'utf8'))
    const hexes = source.match(/#[0-9a-fA-F]{3,8}\b/g) || []
    assert.deepEqual(
      hexes,
      [],
      `${file} 里还有写死的十六进制颜色，它们不会跟随主题变化: ${hexes.slice(0, 5).join(', ')}`,
    )
    const rgbs = source.match(/rgba?\([0-9][^)]*\)/g) || []
    assert.deepEqual(
      rgbs,
      [],
      `${file} 里还有写死的 rgb() 颜色，深色主题下会失效: ${rgbs.slice(0, 5).join(', ')}`,
    )
  }
})

test('组件内样式也使用已定义的主题变量', () => {
  const allCss = fs.readFileSync(new URL('./styles.css', import.meta.url), 'utf8')
    + fs.readdirSync(new URL('./styles/', import.meta.url)).filter(file => file.endsWith('.css'))
      .map(file => fs.readFileSync(new URL(`./styles/${file}`, import.meta.url), 'utf8')).join('\n')
  const defined = new Set([...allCss.matchAll(/(--[\w-]+)\s*:/g)].map(match => match[1]))
  for (const file of fs.readdirSync(new URL('./components/', import.meta.url)).filter(file => file.endsWith('.vue'))) {
    const source = fs.readFileSync(new URL(`./components/${file}`, import.meta.url), 'utf8')
    const css = [...source.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/g)].map(match => match[1]).join('\n')
    for (const match of css.matchAll(/(--[\w-]+)\s*:/g)) defined.add(match[1])
    assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b|rgba?\(\s*\d|:\s*(?:white|black)\b/, `${file} 含不随主题变化的颜色`)
    const missing = [...css.matchAll(/var\(\s*(--[\w-]+)/g)].map(match => match[1]).filter(name => !defined.has(name))
    assert.deepEqual([...new Set(missing)], [], `${file} 使用了未定义的主题变量`)
  }
})

test('字号切换应用到根元素，固定字号也参与文本缩放', () => {
  const app = fs.readFileSync(new URL('./App.vue', import.meta.url), 'utf8')
  assert.match(app, /document\.documentElement\.dataset\.fontSize\s*=/)
  for (const size of ['small', 'medium', 'large']) assert.ok(tokens.includes(`:root[data-font-size="${size}"]`))
  const files = ['styles.css', ...fs.readdirSync(new URL('./styles/', import.meta.url)).filter(f => f.endsWith('.css')).map(f => `styles/${f}`)]
  for (const file of files) {
    const css = fs.readFileSync(new URL(`./${file}`, import.meta.url), 'utf8')
    assert.doesNotMatch(css, /font-size:\s*[\d.]+px\b/, `${file} 的固定字号未参与缩放`)
  }
})
