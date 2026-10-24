import assert from 'node:assert/strict'
import test from 'node:test'
import { sourceIsConfigured, installedBuildLabel } from './selfUpgradePresentation.js'

test('new installation must bind complete source directories before creating a modification', () => {
  assert.equal(sourceIsConfigured(null), false)
  assert.equal(sourceIsConfigured({ configuration: { revision: 0, backend_root: '', desktop_root: '' } }), false)
  assert.equal(sourceIsConfigured({ configuration: { revision: 1, backend_root: 'backend', desktop_root: ' ' } }), false)
  assert.equal(sourceIsConfigured({ configuration: { revision: 1, backend_root: 'backend', desktop_root: 'desktop' } }), true)
})

test('build labels never stringify an object into the UI', () => {
  assert.equal(installedBuildLabel({ source_mode: true, build_id: '' }), '源码运行')
  assert.equal(installedBuildLabel({ source_mode: false, sources: {} }), '未登记构建版本')
  assert.equal(installedBuildLabel({ version: '0.4.1', build_id: 'build' }), '0.4.1')
  assert.equal(installedBuildLabel({ build_id: 'build' }), 'build')
})
