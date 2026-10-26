import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { computed, ref } from 'vue'
import { activityExpanded, settleResponseStream } from './runActivityState.js'
import { openWorkStream } from './services/tasksApi.js'

test('task progress resumes its cursor and closes native streams on idle, replacement and unmount', () => {
  const streams = [], watchers = [], mounted = [], unmounted = []
  class EventSourceFixture {
    constructor(url) { this.url = url; this.listeners = {}; streams.push(this) }
    addEventListener(name, callback) { this.listeners[name] = callback }
    close() { this.closed = true }
  }
  const props = { taskId: 'task/一', task: { status: 'running' } }
  const source = readFileSync(new URL('./components/AgentTaskProgress.vue', import.meta.url), 'utf8')
    .match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
  const context = {
    ref, computed, activityExpanded, settleResponseStream, EventSource: EventSourceFixture,
    defineProps: () => props, defineEmits: () => () => {},
    watch: (getter, callback) => watchers.push(callback),
    onMounted: callback => mounted.push(callback), onUnmounted: callback => unmounted.push(callback),
    tasksApi: { openWorkStream, loadWorkInputsByAfterVersion: async () => ({ inputs: [] }) },
    mergeLoadedItems: (previous, next) => next,
  }
  vm.runInNewContext(source + '\nthis.result = { connect, connected };', context)
  mounted.forEach(callback => callback())
  assert.equal(streams[0].url, `/api/agent/work/${encodeURIComponent(props.taskId)}/stream?after_id=0`)
  streams[0].onopen(); assert.equal(context.result.connected.value, true)
  streams[0].listeners.progress({ data: JSON.stringify({ id: 10, event_type: 'planner_started' }) })
  streams[0].listeners.idle(); assert.equal(streams[0].closed, true)
  context.result.connect()
  assert.ok(streams[1].url.endsWith('after_id=10'))
  const oldOpen = streams[1].onopen
  props.taskId = 'replacement'; watchers[0]()
  assert.equal(streams[1].closed, true)
  assert.equal(streams[2].url, '/api/agent/work/replacement/stream?after_id=0')
  oldOpen(); assert.equal(context.result.connected.value, false)
  unmounted.forEach(callback => callback())
  assert.equal(streams[2].closed, true)
  context.result.connect(); assert.equal(streams.length, 3)
})
