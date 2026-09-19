import test from 'node:test'
import assert from 'node:assert/strict'
import { createUpdateClient } from './composables/useUpdates.js'

const deferred = () => {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

test('browser preview has no installation bridge and does not pretend success', async () => {
  const client = createUpdateClient(() => undefined)
  await client.refresh()
  assert.equal(client.available.value, false)
  assert.equal(await client.invoke('install_update', true), false)
  assert.match(client.error.value, /桌面安装版/)
})

test('update status comes from the native service', async () => {
  const client = createUpdateClient(() => ({
    update_status: async () => ({ ok: true, status: { state: 'available', release: { version: '0.3.1' } } }),
  }))
  await client.refresh()
  assert.equal(client.available.value, true)
  assert.equal(client.state.value.release.version, '0.3.1')
})

test('failed checks stay failed and expose the actual service error', async () => {
  const client = createUpdateClient(() => ({
    check_updates: async () => ({ ok: false, status: { state: 'failed' }, error: '清单签名验证失败' }),
  }))
  assert.equal(await client.invoke('check_updates'), false)
  assert.equal(client.state.value.state, 'failed')
  assert.match(client.error.value, /签名验证失败/)
  assert.equal(client.busy.value, false)
})

test('an earlier status poll cannot overwrite a newer download command', async () => {
  const pending = deferred()
  const client = createUpdateClient(() => ({
    update_status: () => pending.promise,
    download_update: async () => ({ ok: true, status: { state: 'downloading', downloaded: 0, total: 200 } }),
  }))
  const polling = client.refresh()
  assert.equal(await client.invoke('download_update'), true)
  pending.resolve({ ok: true, status: { state: 'available' } })
  await polling
  assert.equal(client.state.value.state, 'downloading')
})

test('the UI cannot send arbitrary commands through the updater client', async () => {
  let called = false
  const client = createUpdateClient(() => ({ execute: async () => { called = true } }))
  await assert.rejects(client.invoke('execute', 'cmd.exe'), /不支持/)
  assert.equal(called, false)
})

test('installation is never triggered by checking or downloading', async () => {
  const commands = []
  const client = createUpdateClient(() => ({
    check_updates: async () => { commands.push('check'); return { ok: true, status: { state: 'available' } } },
    download_update: async () => { commands.push('download'); return { ok: true, status: { state: 'ready' } } },
    install_update: async confirmed => { commands.push(['install', confirmed]); return { ok: true, status: { state: 'installing' } } },
  }))
  await client.invoke('check_updates')
  await client.invoke('download_update')
  assert.deepEqual(commands, ['check', 'download'])
  await client.invoke('install_update', true)
  assert.deepEqual(commands[2], ['install', true])
})

test('progress is bounded and active states are reported as working', async () => {
  const client = createUpdateClient(() => ({}))
  client.state.value = { state: 'downloading', downloaded: 500, total: 200 }
  assert.equal(client.progress.value, 100)
  assert.equal(client.working.value, true)
  client.state.value = { state: 'ready', downloaded: 0, total: 0 }
  assert.equal(client.progress.value, 0)
  assert.equal(client.working.value, false)
})

test('native commands are single-flight and busy resets after completion', async () => {
  const pending = deferred()
  let count = 0
  const client = createUpdateClient(() => ({
    check_updates: async () => { count++; return pending.promise },
  }))
  const first = client.invoke('check_updates')
  assert.equal(await client.invoke('check_updates'), false)
  assert.equal(count, 1)
  pending.resolve({ ok: true, status: { state: 'available' } })
  assert.equal(await first, true)
  assert.equal(client.busy.value, false)
})

test('network preferences and cancellation use fixed native methods', async () => {
  const calls = []
  const client = createUpdateClient(() => ({
    configure_updates: async values => { calls.push(['configure', values]); return { ok: true, status: { state: 'idle', ...values } } },
    cancel_update: async () => { calls.push(['cancel']); return { ok: true, status: { state: 'cancelled' } } },
  }))
  await client.invoke('configure_updates', { auto_check: false, auto_download: false })
  await client.invoke('cancel_update')
  assert.deepEqual(calls, [['configure', { auto_check: false, auto_download: false }], ['cancel']])
  assert.equal(client.state.value.state, 'cancelled')
})
