/**
 * 主题目录 —— 全应用唯一的主题清单。
 *
 * 每个主题对应 styles/tokens.css 里一个 [data-theme="<id>"] 块。
 * 新增主题只需要：
 *   1. 在 tokens.css 加一个 [data-theme="<id>"] 块
 *   2. 在下面这个数组里加一条
 * 不需要改任何 CSS 选择器。
 *
 * mode 用于在设置页把浅色和深色分组展示。
 * swatch 是选择器里显示的三色预览条，取该主题的底色 / 表面色 / 强调色。
 */
export const THEME_OPTIONS = [
  {
    id: 'xuan',
    label: '宣纸',
    description: '暖白纸感 · 松烟墨',
    mode: 'light',
    swatch: ['#f3efe9', '#fdfbf7', '#4a6b74'],
  },
  {
    id: 'celadon',
    label: '青瓷',
    description: '青灰釉色 · 温润',
    mode: 'light',
    swatch: ['#eef2f1', '#fbfdfc', '#45806f'],
  },
  {
    id: 'bamboo',
    label: '竹青',
    description: '苔绿与暖白',
    mode: 'light',
    swatch: ['#eff2ec', '#fcfdf9', '#5f7f4a'],
  },
  {
    id: 'rose',
    label: '蔷薇',
    description: '柔粉与葡萄灰',
    mode: 'light',
    swatch: ['#f4eff1', '#fdfafb', '#a3627a'],
  },
  {
    id: 'mist',
    label: '晨雾',
    description: '中性灰白 · 静蓝',
    mode: 'light',
    swatch: ['#f2f4f6', '#fcfdfd', '#3d6b96'],
  },
  {
    id: 'clay',
    label: '陶土',
    description: '暖沙与陶土红',
    mode: 'light',
    swatch: ['#f4ede6', '#fdf9f4', '#a8683f'],
  },
  {
    id: 'autumn',
    label: '秋叶',
    description: '赭石与枯叶黄',
    mode: 'light',
    swatch: ['#f3eee6', '#fdfaf3', '#996a2f'],
  },
  {
    id: 'ink',
    label: '墨夜',
    description: '深墨底 · 冷青灰',
    mode: 'dark',
    swatch: ['#1a1a1d', '#26262b', '#7fa5b0'],
  },
  {
    id: 'abyss',
    label: '深海',
    description: '深海蓝 · 静默',
    mode: 'dark',
    swatch: ['#12181d', '#1c262e', '#6fa8bd'],
  },
  {
    id: 'dusk',
    label: '暗樱',
    description: '深紫褐 · 樱色',
    mode: 'dark',
    swatch: ['#1c1618', '#2a2225', '#c98ba0'],
  },
]

export const VALID_THEME_IDS = new Set(THEME_OPTIONS.map((item) => item.id))

export const DEFAULT_THEME_ID = 'xuan'

/** 主题按明暗分组，供设置页分组展示。 */
export const THEME_GROUPS = [
  { label: '浅色', items: THEME_OPTIONS.filter((item) => item.mode === 'light') },
  { label: '深色', items: THEME_OPTIONS.filter((item) => item.mode === 'dark') },
]

/**
 * 旧主题 id → 新主题 id。
 *
 * 早期版本只有 mist / clear / rose / forest / sakura 五个主题。用户
 * localStorage 里可能还存着这些值，直接按新清单校验会判定为非法并回退到
 * 默认主题，等于把用户的偏好悄悄重置了。这里做一次就近映射。
 */
const LEGACY_THEME_MAP = {
  clear: 'celadon',
  forest: 'bamboo',
  sakura: 'rose',
}

/**
 * 把任意来源的主题 id 归一到当前合法的主题 id。
 * 旧 id 走映射表，认不出来的回退到默认主题。
 */
export function normalizeThemeId(value) {
  if (VALID_THEME_IDS.has(value)) return value
  const mapped = LEGACY_THEME_MAP[value]
  if (mapped && VALID_THEME_IDS.has(mapped)) return mapped
  return DEFAULT_THEME_ID
}
