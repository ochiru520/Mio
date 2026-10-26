import test from 'node:test'
import assert from 'node:assert/strict'
import { createLearningDraftWriter, learningDraftKey, mergeLearningDraft } from './learningDraftWriter.js'

const storage = () => { const values = new Map(); return { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) } }
test('保存期间继续输入会串行写入，使用最新服务端版本',async()=>{
  const writes=[];let release
  const writer=createLearningDraftWriter(async value=>{writes.push(value);if(writes.length===1)await new Promise(resolve=>{release=resolve});return {revision:writes.length}},0)
  writer.change({answers:{a:'第一版'}})
  const saving=writer.flush();await Promise.resolve();await Promise.resolve()
  writer.change({answers:{a:'第二版'}});release();await saving
  assert.equal(writes.length,2);assert.equal(writes[1].expected_revision,1);assert.equal(writes[1].answers.a,'第二版');assert.equal(writer.dirty,false)
})
test('保存冲突保留未保存状态，重试不会伪装成功',async()=>{
  const writer=createLearningDraftWriter(async()=>{throw new Error('conflict')},2)
  writer.change({answers:{a:'仍需保留'}})
  await assert.rejects(writer.flush(),/conflict/);assert.equal(writer.dirty,true);assert.equal(writer.revision,2)
})

test('保存失败卸载后按项目和会话恢复，同步成功清理临时副本', async () => {
  const store = storage(), key = learningDraftKey('recover-project', 'session')
  const first = createLearningDraftWriter(async () => { throw new Error('offline') }, 3, { storage: store, storageKey: key, initialPayload: { answers: {}, flagged_item_ids: [] } })
  first.change({ answers: { q: '未提交原文' }, active_item_id: 'q', flagged_item_ids: ['q'] })
  await assert.rejects(first.flush(), /offline/)
  assert.match(store.getItem(key), /未提交原文/)
  const isolated = createLearningDraftWriter(async () => {}, 0, { storage: store, storageKey: learningDraftKey('other-project', 'session') })
  assert.equal(isolated.recovered, false)
  const restored = createLearningDraftWriter(async payload => { assert.equal(payload.expected_revision, 3); return { revision: 4 } }, 9, { storage: store, storageKey: key })
  assert.equal(restored.recovered, true)
  assert.equal(restored.payload.answers.q, '未提交原文')
  assert.equal(restored.revision, 3) // Newer remote data must cause a conflict, not an implicit overwrite.
  await restored.flush()
  assert.equal(store.getItem(key), null)
  assert.equal(restored.dirty, false)
})

test('旧组件迟到保存成功不能清除重开组件的新草稿', async () => {
  const store = storage(), key = learningDraftKey('late-project', 'session')
  let release
  const old = createLearningDraftWriter(() => new Promise(resolve => { release = resolve }), 0, { storage: store, storageKey: key })
  old.change({ answers: { q: '旧答案' } }); const saving = old.flush(); await Promise.resolve()
  const current = createLearningDraftWriter(async () => { throw new Error('conflict') }, 0, { storage: store, storageKey: key })
  current.change({ answers: { q: '重开后的新答案' } })
  release({ revision: 1 }); await saving
  assert.equal(JSON.parse(store.getItem(key)).payload.answers.q, '重开后的新答案')
  assert.equal(current.dirty, true)
})

test('显式合并保留本地修改并采用未改题目的远端答案和标记', () => {
  const base = { answers: { a: '原A', b: '原B' }, flagged_item_ids: ['a'], active_item_id: 'a' }
  const local = { answers: { a: '本地A', b: '原B' }, flagged_item_ids: ['a', 'c'], active_item_id: 'c' }
  const remote = { answers: { a: '远端A', b: '远端B', d: '远端D' }, flagged_item_ids: ['b'], active_item_id: 'b', revision: 5 }
  const merged = mergeLearningDraft(base, local, remote)
  assert.deepEqual(merged.answers, { a: '本地A', b: '远端B', d: '远端D' })
  assert.deepEqual(merged.flagged_item_ids.sort(), ['b', 'c'])
  const writer = createLearningDraftWriter(async () => {}, 2, { initialPayload: base })
  writer.change(local); assert.deepEqual(writer.rebase(remote), merged)
  assert.equal(writer.revision, 5); assert.equal(writer.dirty, true)
})

test('保存期间的新输入始终留在临时副本直到下一次写入成功', async () => {
  const store = storage(), key = learningDraftKey('inflight-project', 'session'), releases = []
  const writer = createLearningDraftWriter(() => new Promise(resolve => releases.push(resolve)), 0, { storage: store, storageKey: key })
  writer.change({ answers: { q: '第一版' } }); const saving = writer.flush(); await Promise.resolve()
  writer.change({ answers: { q: '第二版' } }); releases[0]({ revision: 1 })
  await new Promise(resolve => setImmediate(resolve))
  const pending = JSON.parse(store.getItem(key))
  assert.equal(pending.payload.answers.q, '第二版'); assert.equal(pending.revision, 1)
  releases[1]({ revision: 2 }); await saving
  assert.equal(store.getItem(key), null)
})

test('临时存储不可写仍可在同一页面进程重建草稿', () => {
  const store = { getItem() { throw new Error('blocked') }, setItem() { throw new Error('quota') }, removeItem() {} }
  const key = learningDraftKey('memory-fallback', 'session')
  const first = createLearningDraftWriter(async () => {}, 0, { storage: store, storageKey: key })
  first.change({ answers: { q: '仍保留' } })
  assert.match(first.storageError, /临时存储不可用/)
  const restored = createLearningDraftWriter(async () => {}, 0, { storage: store, storageKey: key })
  assert.equal(restored.payload.answers.q, '仍保留')
})
