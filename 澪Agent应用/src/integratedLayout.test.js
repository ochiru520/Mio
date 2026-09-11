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

test('chat composer stays attached to the bottom at desktop widths', () => {
  assert.match(
    css,
    /\.integrated-chat-layout \.chat-column\s*\{[^}]*grid-template-rows:\s*minmax\(0,\s*1fr\) auto;[^}]*overflow:\s*hidden;/,
  )
  assert.match(
    css,
    /\.integrated-chat-layout \.message-scroll\s*\{[^}]*min-height:\s*0;[^}]*overflow:\s*auto;/,
  )
  assert.match(
    css,
    /\.integrated-chat-layout \.composer-wrap\s*\{[^}]*grid-row:\s*2;[^}]*align-self:\s*end;[^}]*padding:\s*8px clamp\(18px,\s*3vw,\s*38px\) 8px;/,
  )
  assert.doesNotMatch(
    css,
    /\.integrated-chat-layout \.composer-wrap\s*\{\s*padding:\s*16px clamp\(56px,\s*7vw,\s*120px\) 20px;/,
  )
  assert.match(
    css,
    /\.integrated-chat-layout \.composer\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;/,
  )
  assert.match(
    css,
    /\.integrated-chat-layout \.composer-actions\s*\{\s*margin-top:\s*auto;\s*\}/,
  )
})

test('Agent home keeps actions and workflow status visible at narrow widths', () => {
  const agentCss = fs.readFileSync(new URL('./styles/agent-workspace.css', import.meta.url), 'utf8')

  assert.match(agentCss, /\.agent-home-header\s*\{[^}]*flex-direction:\s*column;/)
  assert.match(agentCss, /\.agent-status-band\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\);/)
  assert.match(agentCss, /\.agent-status-band\s*>\s*div\s*\{[^}]*min-width:\s*0;/)
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
  assert.match(agentCss, /grid-template-columns:\s*64px minmax\(0, 1fr\) 92px;/)
})

test('both settings titlebars keep window controls in place and hide workspace switches', () => {
  const appSource = fs.readFileSync(new URL('./App.vue', import.meta.url), 'utf8')

  assert.match(appSource, /v-if="activeView !== 'settings'" class="workspace-title-switch"/)
  assert.match(css, /active-view-settings \.integrated-titlebar > \.window-controls\s*\{\s*grid-column:\s*2;/)
})
