import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const css = fs.readFileSync(new URL('./styles/integrated.css', import.meta.url), 'utf8')

test('workspace stays in the visible content row when the left sidebar is hidden', () => {
  assert.match(
    css,
    /\.app-shell\.integrated-shell\.left-sidebar-hidden \.integrated-workspace\s*\{[^}]*grid-column:\s*1;[^}]*grid-row:\s*2;/,
  )
})

test('workspace fills the shell when both sidebars are hidden', () => {
  assert.match(
    css,
    /\.app-shell\.integrated-shell\.left-sidebar-hidden\.right-sidebar-hidden \.integrated-workspace\s*\{[^}]*grid-column:\s*1;[^}]*grid-row:\s*2;/,
  )
})

/**
 * 读取某个选择器对应的规则块。
 * 断言布局意图而不是具体数值——数值会随设计调整变化，意图不会。
 */
function ruleFor(source, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = source.match(new RegExp(escaped + '\\s*\\{([^}]*)\\}'))
  return match ? match[1] : ''
}

// Composer placement is verified against actual browser geometry, including the
// optional Agent handoff notice, in scripts/test_chat_handoff_layout_ui.cjs.

test('Agent home keeps actions and workflow status visible at narrow widths', () => {
  const agentCss = fs.readFileSync(new URL('./styles/agent-workspace.css', import.meta.url), 'utf8')

  // 意图：窄屏下首页头部改为纵向堆叠，操作按钮不会被挤出可视区
  const narrowHome = agentCss.match(/@media[^{]*max-width:\s*760px[^{]*\{([\s\S]*?)\n\}/)
  assert.ok(narrowHome, 'Agent 首页应当有针对窄屏的媒体查询')
  assert.match(ruleFor(narrowHome[1], '.agent-home-header'), /flex-direction:\s*column/)

  // 意图：状态带在窄屏改成两列，且每格允许收缩，避免横向溢出
  const band = ruleFor(narrowHome[1], '.agent-status-band')
  assert.match(band, /grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/)
  assert.match(ruleFor(narrowHome[1], '.agent-status-band > div'), /min-width:\s*0/)
})

test('Agent workspace has its own right rail and settings while main navigation has no creation entries', () => {
  const appSource = fs.readFileSync(new URL('./App.vue', import.meta.url), 'utf8')
  const agentRailSource = fs.readFileSync(new URL('./components/AgentRightRail.vue', import.meta.url), 'utf8')
  const agentSettingsSource = fs.readFileSync(new URL('./components/AgentSettingsPage.vue', import.meta.url), 'utf8')
  const agentCss = fs.readFileSync(new URL('./styles/agent-workspace.css', import.meta.url), 'utf8')
  const mainNav = appSource.match(/const mainNavItems = \[([\s\S]*?)\n\]/)?.[1] || ''

  assert.doesNotMatch(mainNav, /creation|创作/)
  assert.match(appSource, /\{ id: 'settings', label: '设置', icon: Settings \}/)
  assert.match(appSource, /<AgentRightRail v-if="isAgentWorkspace"/)
  assert.match(agentRailSource, /启动并连接/)
  assert.match(agentSettingsSource, /AgentCreationSettings/)
  assert.match(fs.readFileSync(new URL('./components/AgentCreationSettings.vue', import.meta.url), 'utf8'), /启动并连接 ComfyUI/)
  // 意图：Agent 外壳是「窄导航 + 主区 + 窄工具栏」三列，两侧都比主工作区窄
  const agentShell = ruleFor(agentCss, '.app-shell.integrated-shell.agent-workspace-shell')
  assert.match(agentShell, /grid-template-columns:\s*64px minmax\(0,\s*1fr\) 92px/)
})

test('Agent workbench keeps Codex-style task entry and only exposes Mio routes', () => {
  const appSource = fs.readFileSync(new URL('./App.vue', import.meta.url), 'utf8')
  const homeSource = fs.readFileSync(new URL('./components/AgentHomePage.vue', import.meta.url), 'utf8')
  const agentCss = fs.readFileSync(new URL('./styles/agent-workspace.css', import.meta.url), 'utf8')
  assert.match(appSource, /'agent-codex-layout': isAgentWorkspace/)
  assert.match(homeSource, /aria-label="描述要完成的任务"/)
  assert.match(homeSource, /context\.draft = text/)
  assert.match(homeSource, /emit\('navigate', 'projects'\)/)
  assert.match(homeSource, /emit\('navigate', 'tasks'\)/)
  assert.match(agentCss, /agent-codex-layout \{ grid-template-columns: 224px minmax\(0, 1fr\) 286px/)
  assert.match(agentCss, /\.agent-suggestions \{ display: grid; grid-template-columns: repeat\(3, minmax\(0, 1fr\)\)/)
  assert.match(agentCss, /@media \(max-width: 760px\)[\s\S]*agent-codex-layout \{ grid-template-columns: 52px minmax\(0, 1fr\) 68px/)
})

test('both settings titlebars keep window controls in place and hide workspace switches', () => {
  const appSource = fs.readFileSync(new URL('./App.vue', import.meta.url), 'utf8')

  assert.match(appSource, /v-if="activeView !== 'settings'" class="workspace-title-switch"/)
  assert.match(css, /active-view-settings \.integrated-titlebar > \.window-controls\s*\{\s*grid-column:\s*2;/)
})

test('main titlebar uses the native drag bridge as well as the Agent titlebar', () => {
  const appSource = fs.readFileSync(new URL('./App.vue', import.meta.url), 'utf8')
  const navigationSource = fs.readFileSync(new URL('./composables/useDesktopNavigation.js', import.meta.url), 'utf8')
  assert.match(appSource, /class="window-drag-zone pywebview-drag-region" @pointerdown\.left="beginDesktopWindowDrag"/)
  assert.match(navigationSource, /function beginDesktopWindowDrag\(event\)/)
  assert.match(navigationSource, /window\.pywebview\?\.api\?\.window_drag/)
})

test('Agent titlebar owns pointer dragging while controls remain protected', () => {
  const agentCss = fs.readFileSync(new URL('./styles/agent-surface.css', import.meta.url), 'utf8')
  const agentSource = fs.readFileSync(new URL('./components/AgentSurface.vue', import.meta.url), 'utf8')
  const dragSource = fs.readFileSync(new URL('./desktopWindowDrag.js', import.meta.url), 'utf8')
  assert.match(agentCss, /\.mio-agent-titlebar \{[^}]*-webkit-app-region:\s*no-drag/)
  assert.match(agentCss, /\.mio-agent-drag-strip\s*\{[^}]*-webkit-app-region:\s*no-drag/)
  assert.match(agentCss, /\.mio-agent-titlebar > button, \.mio-agent-titlebar nav, \.mio-agent-window-controls[^}]*-webkit-app-region:\s*no-drag/)
  assert.match(agentSource, /class="mio-agent-titlebar pywebview-drag-region" @pointerdown\.left="beginWindowDrag"/)
  assert.match(agentSource, /mio-agent-drag-strip pywebview-drag-region[^>]*@pointerdown\.left="beginWindowDrag"/)
  assert.doesNotMatch(agentSource, /@mousedown\.left\.stop="beginWindowDrag"/)
  assert.match(agentSource, /value !== mode\.value/)
  assert.match(agentSource, /beginWindowMove/)
  assert.match(dragSource, /eventTarget\.addEventListener\('pointermove', update, options\)/)
  assert.match(dragSource, /const fallback = window\.pywebview\?\.api\?\.window_drag/)
})

test('image mode passes its mode to the Agent tool picker for an image default', () => {
  const chatSource = fs.readFileSync(new URL('./components/ChatPage.vue', import.meta.url), 'utf8')
  const pickerSource = fs.readFileSync(new URL('./components/AgentToolPicker.vue', import.meta.url), 'utf8')
  assert.match(chatSource, /AgentToolPicker[^>]*:mode="surface\?\.mode \|\| \(context\.isAgentWorkspace \? 'work' : ''\)"/)
  assert.match(pickerSource, /props\.mode !== 'image'/)
  assert.match(pickerSource, /preferredImageWorkflow/)
})
