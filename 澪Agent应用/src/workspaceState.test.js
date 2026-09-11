import assert from 'node:assert/strict'
import test from 'node:test'

import { filterConversationsForWorkspace, normalizeAgentView } from './workspaceState.js'

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
