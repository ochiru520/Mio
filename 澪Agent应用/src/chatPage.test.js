import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const root = path.dirname(fileURLToPath(import.meta.url))
const chatPageSource = fs.readFileSync(path.join(root, 'components', 'ChatPage.vue'), 'utf8')
const appSource = fs.readFileSync(path.join(root, 'App.vue'), 'utf8')
const chatCssSource = fs.readFileSync(path.join(root, 'styles', 'chat.css'), 'utf8')
const agentCssSource = fs.readFileSync(path.join(root, 'styles', 'agent-workspace.css'), 'utf8')
const integratedCssSource = fs.readFileSync(path.join(root, 'styles', 'integrated.css'), 'utf8')

test('unpinned conversation drawer closes from the outside backdrop', () => {
  assert.match(chatPageSource, /conversationDrawerOpen && !conversationDrawerPinned/)
  assert.match(chatPageSource, /class="conversation-drawer-backdrop"/)
  assert.match(chatPageSource, /@click="conversationDrawerOpen = false"/)
  assert.match(integratedCssSource, /\.conversation-drawer-backdrop \{[^}]*z-index: 11;[^}]*inset: 0;/)
})

test('QQ shared conversation supports selecting individual messages to delete', () => {
  assert.match(chatPageSource, /title="选择要清理的 QQ 消息"/)
  assert.match(chatPageSource, /role="checkbox"/)
  assert.match(chatPageSource, /selectedQqMessageIds\.includes\(Number\(part\.id\)\)/)
  assert.match(chatPageSource, /deleteSelectedQqMessages/)
  assert.match(chatPageSource, /context\.deleteConversationMessages/)
})

test('ordinary chat hides Agent execution receipts and cannot enable creation tools', () => {
  assert.doesNotMatch(chatPageSource, /message-tool-receipts/)
  assert.doesNotMatch(chatPageSource, /creation-tools-toggle/)
  assert.match(chatPageSource, /context\.isAgentWorkspace && turn\.role === 'assistant'/)
  assert.match(appSource, /creation_tools_enabled:\s*isAgentWorkspace/)
  assert.match(appSource, /mode:\s*isAgentWorkspace \? 'agent' : 'companion'/)
  assert.doesNotMatch(appSource, /isAgentWorkspace \|\| creationToolsEnabled/)
})

test('chat images open in an in-app large preview instead of a raw browser tab', () => {
  assert.match(chatPageSource, /class="message-attachment image message-image-preview"/)
  assert.match(chatPageSource, /<ImagePreview v-if="activeImageAttachment"/)
  assert.match(chatPageSource, /@click="openImagePreview\(attachment\)"/)
  assert.doesNotMatch(chatPageSource, /target="_blank"/)
  assert.match(chatCssSource, /\.message-attachment\.image\s*\{[^}]*width:\s*fit-content;/)
  assert.match(chatCssSource, /\.message-attachment\.image img\s*\{[^}]*width:\s*auto;[^}]*height:\s*auto;/)
  assert.doesNotMatch(chatCssSource, /\.message-attachment\.image img\s*\{[^}]*background:\s*#eef2f2;/)
  assert.match(agentCssSource, /\.agent-workspace-shell \.message-attachment\.image\s*\{[^}]*width:\s*fit-content;/)
  assert.match(agentCssSource, /\.agent-workspace-shell \.message-attachment\.image img\s*\{[^}]*width:\s*auto;[^}]*height:\s*auto;/)
  assert.match(chatCssSource, /\.chat-image-lightbox\s*\{[^}]*position:\s*fixed;/)
})
