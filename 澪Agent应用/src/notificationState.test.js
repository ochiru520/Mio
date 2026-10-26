import test from 'node:test'
import assert from 'node:assert/strict'
import { createNotificationState, notificationTarget } from './notificationState.js'
const item = (extra = {}) => ({ id: 1, task_id: 'task-a', conversation_id: 'chat-a', actionable: true, ...extra })

test('通知读取合并在途请求，打开后只标记一次展示', async () => {
  let release, reads = 0, presentations = 0
  const state = createNotificationState({ allNotifications: () => { reads++; return new Promise(resolve => { release = resolve }) }, presentNotification: async () => { presentations++ } }, () => true)
  const loading = state.load(); state.open.value = true; const reopened = state.load()
  assert.equal(loading, reopened); await Promise.resolve(); release({ notifications: [item(), item()] }); await loading
  assert.equal(reads, 1); assert.equal(state.visible.value.length, 1); assert.equal(presentations, 1)
  await state.present(); assert.equal(presentations, 1)
})

test('隐藏成功后迟到或重复轮询不能让通知重新出现', async () => {
  let release, reads = 0, dismissed = 0
  const state = createNotificationState({ allNotifications: async () => { reads++; if (reads === 2) return new Promise(resolve => { release = resolve }); return { notifications: [item()] } }, dismissNotification: async () => { dismissed++ } }, () => true)
  await state.load(); const polling = state.load(); await state.dismiss(item()); release({ notifications: [item()] }); await polling
  assert.equal(state.visible.value.length, 0); assert.equal(state.pending.value.length, 0); assert.equal(dismissed, 1)
})

test('不同任务相同通知ID不合并，目标使用所属任务而非不一致的target', async () => {
  const state = createNotificationState({ allNotifications: async () => ({ notifications: [item(), item({ task_id: 'task-b', conversation_id: 'chat-b' })] }) }, () => true)
  await state.load(); assert.equal(state.visible.value.length, 2)
  assert.deepEqual(notificationTarget(item({ target: { task_id: 'wrong-task', conversation_id: 'wrong-chat' } })), { task_id: 'task-a', conversation_id: 'chat-a' })
  assert.equal(notificationTarget({ task_id: 'task-a', target: { task_id: 'task-b', conversation_id: 'chat-b' } }), null)
  assert.equal(state.target({ id: 2 }), null); assert.match(state.error.value, /无法打开/)
})

test('卸载后迟到请求不更新列表，隐藏页面不请求或标记展示', async () => {
  let release
  const state = createNotificationState({ allNotifications: () => new Promise(resolve => { release = resolve }) }, () => true)
  const loading = state.load(); await Promise.resolve(); state.dispose(); release({ notifications: [item()] }); await loading
  assert.deepEqual(state.items.value, [])
  const hidden = createNotificationState({ allNotifications: async () => { throw new Error('must not fetch') } }, () => false)
  assert.equal(await hidden.load(), false); assert.equal(hidden.error.value, '')
})

test('同步读取异常后仍可再次刷新，不留下已结束的锁', async () => {
  let failed = true
  const state = createNotificationState({ allNotifications() { if (failed) throw new Error('synthetic'); return { notifications: [item()] } } }, () => true)
  assert.equal(await state.load(), false); failed = false
  assert.equal(await state.load(), true); assert.equal(state.pending.value.length, 1)
})
