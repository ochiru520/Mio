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
