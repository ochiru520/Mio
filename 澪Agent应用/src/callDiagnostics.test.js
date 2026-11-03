import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { parse, compileScript } from '@vue/compiler-sfc'
import { computed, ref } from 'vue'

function fixture(load) {
  const source = readFileSync(new URL('./components/CallDiagnosticsPanel.vue', import.meta.url), 'utf8')
  const script = compileScript(parse(source).descriptor, { id: 'call-diagnostics' }).content
  const mounted = [], unmounted = [], timers = [], cleared = [], requests = []
  const context = { computed, ref, onMounted: cb => mounted.push(cb), onBeforeUnmount: cb => unmounted.push(cb), AbortController,
    setInterval: (cb, ms) => { timers.push({ cb, ms }); return 77 }, clearInterval: id => cleared.push(id),
    loadCallStatus: async options => { requests.push(options); return load(options) }, component: null }
  vm.runInNewContext(script.replace(/^import .*$/gm, '').replace('export default', 'component ='), context)
  const panel = context.component.setup({}, { expose() {} })
  return { panel, mounted, unmounted, timers, cleared, requests }
}

test('call diagnostics retains only measured timing fields and never stores conversation contents', async () => {
  const f = fixture(async () => ({ active: true, stage: 'speaking', current_turn_id: 2, transcript: 'private input', reply: 'private reply', recording: 'bytes', timings: { asr_ms: 123, model_ms: 1420, model_first_token_ms: 0, server_total_ms: 1600, secret: 'content' }, voice_started: { first_audio_latency_ms: 580, text: 'hidden' } }))
  await f.panel.refresh()
  assert.equal(f.panel.stageLabel.value, '正在说话')
  assert.deepEqual(JSON.parse(JSON.stringify(f.panel.rows.value)).map(r => r.ms), [123, 0, 1420, 1600, 580])
  assert.doesNotMatch(JSON.stringify(f.panel.status.value), /private|secret|hidden|recording|transcript|reply/)
  assert.equal(f.panel.formatMs(1420), '1.42 秒')
  assert.equal(f.panel.formatMs(null), '尚未测得')
})

test('call diagnostics sanitizes unavailable values and refresh errors do not expose server text', async () => {
  let fail = false
  const f = fixture(async () => { if (fail) throw new Error('private transcript'); return { stage: 'private reply', current_turn_id: -1, timings: { asr_ms: -1, model_ms: Infinity, model_first_token_ms: '12' }, voice_started: { first_audio_latency_ms: NaN } } })
  await f.panel.refresh()
  assert.equal(f.panel.stageLabel.value, '状态未确认')
  assert.ok(f.panel.rows.value.every(row => row.ms == null))
  fail = true; await f.panel.refresh()
  assert.match(f.panel.error.value, /暂时无法读取/)
  assert.doesNotMatch(f.panel.error.value, /private/)
})

test('only a mounted panel polls and unmount aborts the request, clears polling and rejects late results', async () => {
  let release
  const f = fixture(() => new Promise(resolve => { release = resolve }))
  assert.equal(f.requests.length, 0)
  f.mounted[0]()
  assert.equal(f.requests.length, 1)
  assert.equal(f.timers[0].ms, 2000)
  f.timers[0].cb(); assert.equal(f.requests.length, 1)
  f.unmounted[0]()
  assert.equal(f.requests[0].signal.aborted, true)
  assert.deepEqual(f.cleared, [77])
  release({ active: true, stage: 'speaking', timings: { asr_ms: 1 } })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(f.panel.status.value.stage, 'idle')
  await f.panel.refresh(); assert.equal(f.requests.length, 1)
})
