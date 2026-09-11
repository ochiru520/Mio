import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { normalizeLoadedChatSettings } from './chatSettingsState.js'

const root = path.dirname(fileURLToPath(import.meta.url))
const appSource = fs.readFileSync(path.join(root, 'App.vue'), 'utf8')

test('后端保存的默认模型覆盖本机残留模型', () => {
  assert.deepEqual(
    normalizeLoadedChatSettings(
      { model_id: 'saved-model', reasoning_level: 'high', voice_language: 'ja' },
      ['saved-model', 'old-local-model'],
    ),
    { model_id: 'saved-model', reasoning_level: 'high', voice_language: 'ja' },
  )
})

test('已经不存在的默认模型安全回退为自动', () => {
  assert.equal(
    normalizeLoadedChatSettings({ model_id: 'removed-model' }, ['available-model']).model_id,
    'auto',
  )
})

test('启动流程只读取共享设置而不反向覆盖后端', () => {
  const start = appSource.indexOf('onMounted(async () => {')
  const end = appSource.indexOf('onBeforeUnmount(() =>', start)
  assert.notEqual(start, -1)
  assert.notEqual(end, -1)
  const startupSource = appSource.slice(start, end)
  assert.match(startupSource, /await loadSharedChatSettings\(\)/)
  assert.doesNotMatch(startupSource, /syncSharedChatSettings\(/)
})
