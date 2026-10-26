import test from 'node:test'
import assert from 'node:assert/strict'
import * as chat from './services/chatApi.js'
import * as companion from './services/companionApi.js'
import * as capabilities from './services/capabilitiesApi.js'
import * as workspace from './services/workspaceApi.js'
import * as execution from './services/executionApi.js'
import * as artifacts from './services/artifactsApi.js'
import * as tasks from './services/tasksApi.js'
import * as qq from './services/qqApi.js'
import * as selfUpgrade from './services/selfUpgradeApi.js'
import { screenPreviewUrl } from './services/companionApi.js'
import { openRealtimeVoiceSocket, realtimeVoiceUrl } from './services/voiceApi.js'

function recorder(result = { ok: true }) {
  const calls = []
  const transport = (url, options) => { calls.push({ url, options }); return Promise.resolve(result) }
  return { calls, transport, result }
}

test('chat services preserve injected transport, caller cancellation and unlimited Agent requests', async () => {
  const { calls, transport } = recorder()
  const signal = new AbortController().signal
  const payload = { message: 'hello', client_request_id: 'request-1' }
  await chat.submitConversationChat(false, payload, { signal, timeoutMs: 0 }, transport)
  await chat.submitConversationChat(true, payload, {}, transport)
  await chat.cancelConversationChat(true, { client_request_id: 'request-1' }, {}, transport)
  assert.equal(calls[0].url, '/api/agent/chat')
  assert.equal(calls[0].options.signal, signal)
  assert.equal(calls[0].options.timeoutMs, 0)
  assert.deepEqual(JSON.parse(calls[0].options.body), payload)
  assert.equal(calls[1].url, '/api/companion/chat')
  assert.equal(calls[2].url, '/api/companion/chat/cancel')
  assert.equal(calls[2].options.deadlineClass, 'mutation')
})

test('domain reads retain an absent options argument for existing injected transports', async () => {
  const { calls, transport, result } = recorder({ capabilities: [], busy: false })
  assert.equal(await capabilities.loadCapabilities({}, transport), result)
  assert.equal(calls[0].url, '/api/capabilities')
  assert.equal(calls[0].options, undefined)
  await capabilities.submitCapabilities('voice', 'check', { consent: true }, {}, transport)
  assert.equal(calls[1].url, '/api/capabilities/voice/check')
  assert.deepEqual(JSON.parse(calls[1].options.body), { consent: true })
  assert.throws(() => capabilities.submitCapabilities('voice', '../other', {}, {}, transport), /Unsupported API operation/)
})

test('project files keep binary content, filename encoding and their original upload deadline', async () => {
  const { calls, transport } = recorder()
  const file = { name: '需求 #1.md' }
  const signal = new AbortController().signal
  await workspace.uploadProjectFile('项目/a', file, { encodeProjectId: true, signal, headers: { 'X-Mio-Mobile': '1' } }, transport)
  assert.equal(calls[0].url, `/api/workspace/projects/${encodeURIComponent('项目/a')}/upload?name=${encodeURIComponent(file.name)}`)
  assert.equal(calls[0].options.body, file)
  assert.equal(calls[0].options.headers['Content-Type'], 'application/octet-stream')
  assert.equal(calls[0].options.headers['X-Mio-Mobile'], '1')
  assert.equal(calls[0].options.signal, signal)
  assert.equal(calls[0].options.timeoutMs, 120000)
})

test('project creation, edits and execution revisions use their own fixed contracts', async () => {
  const { calls, transport } = recorder()
  await workspace.saveProject('', { name: 'new' }, {}, transport)
  await workspace.saveProject('p1', { name: 'edited' }, {}, transport)
  await workspace.saveSkill('id/1', { expected_revision: 3 }, { headers: { 'X-Mio-Mobile': '1' } }, transport)
  const signal = new AbortController().signal
  await execution.saveExecutionPolicy({ expected_revision: 7, enabled: false }, { signal, headers: { 'If-Match': 'revision-7' } }, transport)
  assert.deepEqual(calls.slice(0, 2).map(call => [call.url, call.options.method]), [['/api/workspace/projects', 'POST'], ['/api/workspace/projects/p1', 'PUT']])
  assert.equal(calls[2].url, '/api/workspace/skills/id%2F1')
  assert.equal(calls[2].options.headers['X-Mio-Mobile'], '1')
  assert.deepEqual(JSON.parse(calls[3].options.body), { expected_revision: 7, enabled: false })
  assert.equal(calls[3].options.headers['If-Match'], 'revision-7')
  assert.equal(calls[3].options.signal, signal)
})

test('artifact verification omits a body while review and favorite serialize their own payloads', async () => {
  const { calls, transport } = recorder()
  await artifacts.mutateArtifact('a1', 'verify', undefined, {}, transport)
  await artifacts.mutateArtifact('a1', 'review', { status: 'user_approved', note: 'ok' }, {}, transport)
  await artifacts.mutateArtifact('a1', 'favorite', { favorite: false }, {}, transport)
  assert.equal(Object.hasOwn(calls[0].options, 'body'), false)
  assert.deepEqual(JSON.parse(calls[1].options.body), { status: 'user_approved', note: 'ok' })
  assert.deepEqual(JSON.parse(calls[2].options.body), { favorite: false })
  assert.throws(() => artifacts.mutateArtifact('a1', 'unrelated/path', {}, {}, transport), /Unsupported API operation/)
})

test('task approvals are distinct from the finite pause, resume and cancel commands', async () => {
  const { calls, transport } = recorder()
  await tasks.submitWork('task/1', 'resume', {}, transport)
  await tasks.approveWorkAction('task/1', 'action-2', {}, transport)
  assert.equal(calls[0].url, '/api/agent/work/task%2F1/resume')
  assert.equal(calls[1].url, '/api/agent/work/task%2F1/actions/action-2/approve')
  assert.throws(() => tasks.submitWork('task/1', 'arbitrary/path', {}, transport), /Unsupported API operation/)
})

test('voice packages and QQ images preserve raw responses and binary headers', async () => {
  const response = { ok: false, status: 404, blob() {}, json() {} }
  const { calls, transport } = recorder(response)
  assert.equal(await companion.exportVoicePackage('voice-1', transport), response)
  const file = { name: 'voice.zip' }
  assert.equal(await companion.importVoicePackage(file, transport), response)
  assert.equal(await qq.loadQqQrCodeResponse(123, transport), response)
  assert.deepEqual(JSON.parse(calls[0].options.body), { profile_id: 'voice-1' })
  assert.equal(calls[1].options.body, file)
  assert.equal(calls[1].options.headers['Content-Type'], 'application/octet-stream')
  assert.equal(calls[2].url, '/api/agent/qq/qrcode?ts=123')
  assert.equal(calls[2].options.cache, 'no-store')
})

test('self-upgrade retains its long mutation deadline and rejects an unknown command', async () => {
  const { calls, transport } = recorder()
  await selfUpgrade.controlUpgradeJob('job/1', 'build', {}, transport)
  await selfUpgrade.controlUpgradeJob('job/1', 'apply', { confirmed: true }, transport)
  assert.equal(calls[0].url, '/api/self-upgrade/jobs/job%2F1/build')
  assert.equal(calls[0].options.timeoutMs, 600000)
  assert.deepEqual(JSON.parse(calls[1].options.body), { confirmed: true })
  assert.throws(() => selfUpgrade.controlUpgradeJob('job/1', 'other/path', {}, transport), /Unknown upgrade action/)
})

test('task streams preserve the injected EventSource and cursor, and preview URLs stay domain-owned', () => {
  const streams = []
  class EventSourceFixture { constructor(url) { this.url = url; streams.push(this) } close() { this.closed = true } }
  const stream = tasks.openWorkStream('task/一', 17, EventSourceFixture)
  assert.equal(stream, streams[0])
  assert.equal(stream.url, `/api/agent/work/${encodeURIComponent('task/一')}/stream?after_id=17`)
  stream.close(); assert.equal(stream.closed, true)
  assert.equal(screenPreviewUrl(123), '/api/companion/screen/preview?t=123')
  assert.throws(() => tasks.openWorkStream('task', 0, null), /EventSource is unavailable/)
})

test('realtime voice sockets keep protocol selection, native identity and explicit test URL injection', () => {
  const sockets = []
  class SocketFixture { constructor(url) { this.url = url; sockets.push(this) } close() { this.closed = true } }
  assert.equal(realtimeVoiceUrl({ protocol: 'https:', host: 'mio.example:443' }), 'wss://mio.example:443/api/realtime/voice')
  const socket = openRealtimeVoiceSocket({ protocol: 'http:', host: '127.0.0.1:8000' }, SocketFixture)
  assert.equal(socket, sockets[0])
  assert.equal(socket.url, 'ws://127.0.0.1:8000/api/realtime/voice')
  socket.close(); assert.equal(socket.closed, true)
  assert.equal(openRealtimeVoiceSocket(undefined, SocketFixture, { urlOverride: 'ws://isolated-fixture/voice' }).url, 'ws://isolated-fixture/voice')
})
