import test from 'node:test'
import assert from 'node:assert/strict'
import { executionStatus, taskStatusLabel, canResumeTask } from './agentTaskState.js'

test('successful non-creation tools report execution success', () => {
  assert.equal(executionStatus([{ tool_name: 'search_memory', status: 'completed' }]), '工具执行完成')
})
test('confirmation and failure override success', () => {
  assert.equal(executionStatus([{ status: 'completed' }, { status: 'needs_confirmation' }]), '等待确认')
  assert.equal(executionStatus([{ status: 'failed' }]), '执行遇到问题')
  assert.equal(executionStatus([], { status: 'running' }), null)
})
test('only interrupted states expose resume', () => {
  assert.equal(canResumeTask({ status: 'paused' }), true)
  assert.equal(canResumeTask({ status: 'completed' }), false)
  assert.equal(canResumeTask({ status: 'cancelled' }), false)
  assert.equal(taskStatusLabel('waiting_jobs'), '等待后台结果')
})
