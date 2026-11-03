import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'

const source = fs.readFileSync(new URL('../public/live2d-pet/live2d-pet.js', import.meta.url), 'utf8')
function harness({ bridgePending = false } = {}) {
  const handlers = { canvas: {}, window: {} }
  const captures = new Set()
  const calls = []
  const frames = []
  const context = vm.createContext({
    root: { classList: { add() {}, remove() {}, toggle() {} } },
    canvas: {
      addEventListener(name, fn) { handlers.canvas[name] = fn },
      setPointerCapture(id) { captures.add(id) },
      hasPointerCapture(id) { return captures.has(id) },
      releasePointerCapture(id) { captures.delete(id) },
    },
    window: {
      innerWidth: 1920, innerHeight: 1080,
      addEventListener(name, fn) { handlers.window[name] = fn },
      requestAnimationFrame(fn) { frames.push(fn); return frames.length },
      mioDesktop: { setInteractive(value) { calls.push(value) }, setClickThroughLocked() {} },
    },
    contextMenu: { hidden: true }, sizePanel: { hidden: true },
    waitForBridge: () => bridgePending ? new Promise(() => {}) : Promise.resolve({ begin_drag() {}, end_drag() {} }),
    pointHitsModel: (x, y) => x >= 100 && x < 200 && y >= 100 && y < 300,
    updateSpeechBubblePosition() {}, updateLockControlAnchor() {}, updateContextMenuLabels() {},
    persistCompanionSetting: async () => {}, request: async () => {},
    sendSocket(type) { calls.push(type) }, playMotion() {}, semanticMotion() {},
  })
  const start = source.indexOf('  async function setClickThroughLocked(')
  const end = source.indexOf("  canvas.addEventListener('dblclick'", start)
  vm.runInContext(`let settings={live2d_smart_passthrough:false}; let pointerDown=null,dragged=false,interactiveState=null,passThroughFrame=null,cursorFocusFrame=null;
    let lastPointerPosition={clientX:-1,clientY:-1}; const electronRuntime=true;
    const model={x:120,y:150}; const desktopState={bounds:{width:1920,height:1080}};
    ${source.slice(source.indexOf('  function clickThroughLocked()'), source.indexOf('  function updateContextMenuLabels()'))}
    ${source.slice(start, end)}
    globalThis.inspect=()=>({pointerDown,dragged}); globalThis.refresh=updateMousePassThrough; globalThis.lock=setClickThroughLocked;`, context)
  const event = (extra = {}) => ({ pointerId: 1, button: 0, buttons: 1, clientX: 120, clientY: 150, screenX: 120, screenY: 150, ...extra })
  const flush = () => { while(frames.length) frames.shift()() }
  return { handlers, captures, calls, context, event, flush }
}

test('empty desktop passes clicks through even with legacy smart-mode off', () => {
  const h = harness()
  h.context.refresh(true, h.event({ clientX: 600, clientY: 600 }))
  h.flush()
  assert.equal(h.calls.at(-1), false)
})

for (const termination of ['pointerup', 'pointercancel', 'lostpointercapture', 'blur', 'Escape', 'missing-mouseup']) {
  test(`drag releases capture after ${termination}`, async () => {
    const h = harness()
    await h.handlers.canvas.pointerdown(h.event())
    await h.handlers.canvas.pointermove(h.event({clientX: 400, clientY: 400}))
    if (termination === 'blur') await h.handlers.window.blur()
    else if (termination === 'Escape') h.handlers.window.keydown({key:'Escape'})
    else if (termination === 'missing-mouseup') await h.handlers.canvas.pointermove(h.event({buttons:0,clientX:600,clientY:600}))
    else await h.handlers.canvas[termination](h.event({buttons:0,clientX:400,clientY:400}))
    await Promise.resolve()
    h.flush()
    assert.equal(h.context.inspect().pointerDown, null)
    assert.equal(h.context.inspect().dragged, false)
    assert.equal(h.captures.size, 0)
    assert.equal(h.calls.includes('clicked'), false)
    if (termination !== 'blur' && termination !== 'Escape') assert.equal(h.calls.at(-1) === false || h.calls.at(-1) === 'dragged', true)
  })
}

test('native mouse releases synchronously when desktop bridge never resolves', () => {
  const h = harness({bridgePending:true})
  void h.handlers.canvas.pointerdown(h.event())
  void h.handlers.canvas.pointermove(h.event({clientX:400}))
  void h.handlers.canvas.pointerup(h.event({buttons:0,clientX:500}))
  assert.equal(h.context.inspect().pointerDown, null)
  assert.equal(h.captures.size, 0)
  assert.equal(h.calls.at(-1), false)
})

test('another pointer cannot cancel the active drag; locking releases it', async () => {
  const h = harness()
  await h.handlers.canvas.pointerdown(h.event())
  await h.handlers.canvas.pointerup(h.event({pointerId:2,buttons:0}))
  assert.notEqual(h.context.inspect().pointerDown, null)
  await h.context.lock(true, false)
  h.flush()
  assert.equal(h.context.inspect().pointerDown, null)
  assert.equal(h.captures.size, 0)
  assert.equal(h.calls.includes('clicked'), false)
})
