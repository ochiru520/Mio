import assert from 'node:assert/strict'
import test from 'node:test'

import { ACTIVE_VIEW_HEARTBEAT_MS, buildActiveViewReport } from './activeViewState.js'

test('active view heartbeat heals a restarted backend promptly without a tight loop', () => {
  assert.equal(ACTIVE_VIEW_HEARTBEAT_MS, 5000)
})

test('active view report keeps only settings sections', () => {
  assert.deepEqual(buildActiveViewReport('settings', 'models'), {
    view_id: 'settings',
    section_id: 'models',
    visible: true,
  })
  assert.deepEqual(buildActiveViewReport('chat', 'models', false), {
    view_id: 'chat',
    section_id: '',
    visible: false,
  })
})

test('active view report rejects unknown views and settings sections', () => {
  assert.throws(() => buildActiveViewReport('private-page'), /不支持的主应用页面/)
  assert.throws(() => buildActiveViewReport('settings', 'private-section'), /不支持的设置分区/)
})
