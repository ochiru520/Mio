import assert from 'node:assert/strict'
import test from 'node:test'

import { filterConversationsForWorkspace, findAgentModeConversation, normalizeAgentView } from './workspaceState.js'

test('removed creation bookmarks resume in Agent chat and task view survives restart', () => {
  for (const view of ['creation', 'creation-image', 'creation-video']) {
    assert.equal(normalizeAgentView(view), 'chat')
  }
  assert.equal(normalizeAgentView('tasks'), 'tasks')
  assert.equal(normalizeAgentView('unknown'), 'home')
})

const conversations = [
  { id: 'desktop_main', kind: 'desktop' },
  { id: 'desktop_agent_one', kind: 'agent' },
  { id: 'desktop_pet', kind: 'pet' },
  { id: 'qq_private', kind: 'qq' },
]

test('Agent workspace keeps only Agent conversations', () => {
  assert.deepEqual(
    filterConversationsForWorkspace(conversations, 'agent').map((item) => item.id),
    ['desktop_agent_one'],
  )
})

test('main workspace excludes Agent conversations', () => {
  assert.deepEqual(
    filterConversationsForWorkspace(conversations, 'main').map((item) => item.id),
    ['desktop_main', 'desktop_pet', 'qq_private'],
  )
})

test('workspace filtering tolerates a missing conversation list', () => {
  assert.deepEqual(filterConversationsForWorkspace(undefined, 'agent'), [])
})

test('switching back to work resumes a legacy unmarked conversation instead of replacing its draft', () => {
  const oldWork = { id: 'legacy-work', kind: 'agent' }
  const learning = { id: 'learning', kind: 'agent' }
  const items = [learning, oldWork]
  const index = { modes: { learning: 'learn' }, projects: {} }
  assert.equal(findAgentModeConversation(items, index, 'work'), oldWork)
  assert.equal(findAgentModeConversation(items, index, 'learn'), learning)
  assert.equal(findAgentModeConversation(items, index, 'image'), undefined)
})

test('legacy mode fallback keeps project context and explicit modes separate', () => {
  const items = [{ id: 'other-project' }, { id: 'ungrouped' }, { id: 'this-project' }, { id: 'chat' }]
  const index = { modes: { chat: 'chat' }, projects: { 'other-project': 'p2', 'this-project': 'p1', chat: 'p1' } }
  assert.equal(findAgentModeConversation(items, index, 'work', 'p1')?.id, 'this-project')
  assert.equal(findAgentModeConversation(items, index, 'work')?.id, 'ungrouped')
  assert.equal(findAgentModeConversation(items, index, 'chat', 'p1')?.id, 'chat')
  assert.equal(findAgentModeConversation(undefined, index, 'work'), undefined)
})
