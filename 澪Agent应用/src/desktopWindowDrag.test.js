import test from 'node:test'
import assert from 'node:assert/strict'
import { beginWindowMove, blockStandardWindowDrag } from './desktopWindowDrag.js'

const flush = () => new Promise(resolve => setImmediate(resolve))
function fixture(api, control = false) {
  const host = new EventTarget()
  host.pywebview = { api }
  host.setTimeout = setTimeout
  globalThis.window = host
  globalThis.document = new EventTarget()
  const target = new EventTarget()
  target.closest = () => control
  let captured = false
  target.setPointerCapture = () => { captured = true }
  target.hasPointerCapture = () => captured
  target.releasePointerCapture = () => { captured = false }
  const event = { button: 0, pointerId: 1, screenX: 100, screenY: 200, target,
    currentTarget: target, stopped: false, prevented: false,
    stopPropagation() { this.stopped = true }, preventDefault() { this.prevented = true } }
  function pointer(type, x, y, pointerId = 1) {
    const next = Object.assign(new Event(type), { pointerId, screenX: x, screenY: y })
    target.dispatchEvent(next)
  }
  return { host, target, event, pointer }
}

test('titlebar mousedown starts a native drag when pointerdown was missed', async () => {
  let calls = 0
  const { event } = fixture({ window_drag: async () => { calls++; return { ok: true } } })
  blockStandardWindowDrag(event)
  await flush()
  assert.equal(calls, 1)
  assert.equal(event.prevented, true)
})

test('pointer drag does not also start the mousedown fallback or move from another pointer', async () => {
  const moves = [], drags = []
  const f = fixture({ window_move_by: async (...args) => { moves.push(args); return { ok: true } },
    window_drag: async () => { drags.push(true); return { ok: true } } })
  beginWindowMove(f.event)
  blockStandardWindowDrag(f.event)
  f.pointer('pointermove', 300, 400, 2)
  await flush()
  assert.equal(moves.length, 1)
  f.pointer('pointermove', 120, 210)
  await flush()
  f.pointer('pointerup', 130, 220)
  await flush()
  assert.deepEqual(moves.map(args => [args[0], args[1], args[3]]), [[0, 0, false], [20, 10, false], [30, 20, true]])
  assert.equal(drags.length, 0)
  assert.equal(f.host.__mioWindowMoveActive, false)
  f.pointer('pointermove', 140, 230)
  await flush()
  assert.equal(moves.length, 3)
})

test('titlebar controls keep their normal click and never drag the window', async () => {
  let calls = 0
  const { event } = fixture({ window_drag: async () => { calls++ }, window_move_by: async () => { calls++ } }, true)
  beginWindowMove(event)
  blockStandardWindowDrag(event)
  await flush()
  assert.equal(calls, 0)
  assert.equal(event.stopped, false)
  assert.equal(event.prevented, false)
})

test('native drag rejection is handled and releases the active gesture', async () => {
  const errors = []
  const f = fixture({ window_drag: async () => { throw new Error('host unavailable') } })
  blockStandardWindowDrag(f.event, message => errors.push(message))
  await new Promise(resolve => setTimeout(resolve, 10))
  assert.deepEqual(errors, ['host unavailable'])
  assert.equal(f.host.__mioWindowMoveActive, false)
})

test('blur completes the pointer gesture even without a pointerup', async () => {
  const moves = []
  const f = fixture({ window_move_by: async (...args) => { moves.push(args); return { ok: true } } })
  beginWindowMove(f.event)
  await flush()
  f.host.dispatchEvent(new Event('blur'))
  await flush()
  assert.equal(moves.at(-1)[3], true)
  assert.equal(f.host.__mioWindowMoveActive, false)
  f.pointer('pointermove', 150, 250)
  await flush()
  assert.equal(moves.length, 2)
})

