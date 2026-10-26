/**
 * Mio 图标工厂。
 *
 * 规则（保持"很干净"的全部约束）：
 *   - 24 格网格，只用描边，没有填充、渐变和阴影
 *   - 描边 1.5，圆角端点和转角
 *   - 一个图标用最少的笔画；能用一条线说清楚的不用两条
 *   - 颜色只来自 currentColor，主题和状态色自动跟随
 *
 * 接口与 @lucide/vue 保持一致（size / strokeWidth，其余 attrs 透传到根 svg），
 * 所以组件里 <Search :size="16" class="spin" /> 的写法不需要改。
 */
import { defineComponent, h } from 'vue'

/** 一笔线条 */
export const L = (d) => ['path', { d }]
/** 一个点（零长度线段 + 圆角端点） */
export const Dot = (x, y) => L(`M${x} ${y}h.01`)
export const Circle = (cx, cy, r) => ['circle', { cx, cy, r }]
export const Rect = (x, y, width, height, rx = 0) => ['rect', { x, y, width, height, rx }]
/** 左右镜像（x → 24-x），用于对称图形只写一半。 */
export const Mirror = ([tag, attrs]) => [tag, { ...attrs, transform: 'matrix(-1 0 0 1 24 0)' }]

export function makeIcon(name, parts) {
  return defineComponent({
    name,
    props: {
      size: { type: [Number, String], default: 24 },
      strokeWidth: { type: [Number, String], default: 1.5 },
    },
    setup(props, { attrs }) {
      return () => h(
        'svg',
        {
          xmlns: 'http://www.w3.org/2000/svg',
          width: props.size,
          height: props.size,
          viewBox: '0 0 24 24',
          fill: 'none',
          stroke: 'currentColor',
          'stroke-width': props.strokeWidth,
          'stroke-linecap': 'round',
          'stroke-linejoin': 'round',
          'aria-hidden': attrs['aria-label'] ? undefined : 'true',
          class: 'mio-icon',
        },
        parts.map(([tag, attributes]) => h(tag, attributes)),
      )
    },
  })
}

/** 齿轮的闭合路径：n 个齿，齿根半径 rRoot，齿顶半径 rTip。 */
export function gearPath(n = 6, rRoot = 6.6, rTip = 8.8, cx = 12, cy = 12) {
  const pt = (r, a) => `${(cx + r * Math.cos(a)).toFixed(2)} ${(cy + r * Math.sin(a)).toFixed(2)}`
  const step = (Math.PI * 2) / n
  const root = 0.27
  const tip = 0.19
  let d = ''
  for (let i = 0; i < n; i++) {
    const a = -Math.PI / 2 + i * step
    d += `${i === 0 ? 'M' : 'L'}${pt(rRoot, a - root)}L${pt(rTip, a - tip)}L${pt(rTip, a + tip)}L${pt(rRoot, a + root)}`
    d += `A${rRoot} ${rRoot} 0 0 1 ${pt(rRoot, a + step - root)}`
  }
  return `${d}Z`
}
