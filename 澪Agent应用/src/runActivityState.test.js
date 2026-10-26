import test from 'node:test'
import assert from 'node:assert/strict'
import { activityExpanded, settleResponseStream, changedFilesFromHistory } from './runActivityState.js'
test('过程成功且有正式答复才自动收起，手动选择优先', () => {
  assert.equal(activityExpanded('running', false), true)
  assert.equal(activityExpanded('completed', true), false)
  assert.equal(activityExpanded('completed', false), true)
  assert.equal(activityExpanded('failed', true), true)
  assert.equal(activityExpanded('completed', true, true), true)
  assert.equal(activityExpanded('running', false, false), false)
})
test('已保存正文不被迟到流覆盖，失败保留部分正文', () => {
  let state = settleResponseStream({}, 'response_stream_started', {stream_id:'a'})
  state = settleResponseStream(state, 'response_text_delta', {stream_id:'a',text:'草稿'})
  assert.equal(settleResponseStream(state, 'response_text_delta', {stream_id:'b',text:'错'}).text, '草稿')
  assert.equal(settleResponseStream(state, 'final_response_failed').state, 'abandoned')
  state = settleResponseStream(state, 'final_response_completed', {reply:'保存正文'})
  assert.equal(settleResponseStream(state, 'response_text_delta', {stream_id:'a',text:'重复'}).text, '保存正文')
  assert.equal(settleResponseStream(state, 'response_stream_finished', {stream_id:'a',status:'generated'}).state,'committed')
  assert.equal(settleResponseStream(state, 'final_response_completed', {persistence_state:'abandoned'}).state,'abandoned')
})
test('真实改动只取当前任务已应用记录', () => {
  const result = changedFilesFromHistory([{id:'c',task_id:'t',status:'applied',files:[{path:'a.py',additions:2,deletions:1}]},{id:'bad',task_id:'t',status:'proposed',files:[{path:'bad'}]},{id:'other',task_id:'x',status:'applied',files:[{path:'other'}]}], 't')
  assert.deepEqual(result.map(x=>x.path), ['a.py'])
})

test('同任务多次真实修改累计行数并保留按顺序的review记录',()=>{
  const result=changedFilesFromHistory([
    {id:'latest',task_id:'t',status:'applied',files:[{path:'a.py',additions:3,deletions:2}]},
    {id:'first',task_id:'t',status:'applied',files:[{path:'a.py',additions:2,deletions:1}]},
  ],'t')
  assert.deepEqual(result[0].change_ids,['first','latest'])
  assert.equal(result[0].change_id,'latest')
  assert.equal(result[0].additions,5)
  assert.equal(result[0].deletions,3)
})
