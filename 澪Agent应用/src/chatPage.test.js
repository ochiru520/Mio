import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const root = path.dirname(fileURLToPath(import.meta.url))
const chatPageSource = fs.readFileSync(path.join(root, 'components', 'ChatPage.vue'), 'utf8')
const integratedCssSource = fs.readFileSync(path.join(root, 'styles', 'integrated.css'), 'utf8')

test('unpinned conversation drawer closes from the outside backdrop', () => {
  assert.match(chatPageSource, /conversationDrawerOpen && !conversationDrawerPinned/)
  assert.match(chatPageSource, /class="conversation-drawer-backdrop"/)
  assert.match(chatPageSource, /@click="conversationDrawerOpen = false"/)
  assert.match(integratedCssSource, /\.conversation-drawer-backdrop \{[^}]*z-index: 11;[^}]*inset: 0;/)
})
