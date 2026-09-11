import assert from 'node:assert/strict'
import test from 'node:test'
import {
  creationCostLabel,
  creationLoras,
  creationProgressPercent,
  creationPrompt,
  creationSamplerLabel,
  creationStatusLabel,
  creationWorkflowSnapshot,
  findCreationJob,
  formatCreationElapsed,
} from './agentCreationState.js'

const job = {
  id: 'job_test',
  backend: 'comfyui',
  status: 'running',
  progress: 0.426,
  elapsed_seconds: 72.24,
  generation_cost_source: 'local_comfyui',
  workflow_id: 'anima-2.9b-image',
  spec: {
    prompt: 'soft cinematic portrait',
    negative_prompt: 'blurry, watermark',
    lora_choices: ['highres_boost', 'clear_lineart'],
  },
}

test('reads the live creation job from a generation receipt', () => {
  assert.equal(findCreationJob([
    { tool_name: 'creation_check_workflow', result: { ok: true } },
    { tool_name: 'comfyui_generate_image', result: { job } },
  ]), job)
  assert.equal(findCreationJob([{ tool_name: 'creation_check_workflow' }]), null)
})

test('formats creation progress, elapsed time, prompts and local cost', () => {
  assert.equal(creationProgressPercent(job), 43)
  assert.equal(creationStatusLabel(job), '生成中 43%')
  assert.equal(formatCreationElapsed(job.elapsed_seconds), '1 分 12.2 秒')
  assert.equal(creationCostLabel(job), '本地生成 ¥0.000000')
  assert.deepEqual(creationPrompt(job), {
    positive: 'soft cinematic portrait',
    negative: 'blurry, watermark',
  })
  assert.deepEqual(creationLoras(job), [
    'Turbo v0.1',
    'Turbo v0.2',
    'HighresBoost',
    '清晰线稿',
  ])
  assert.equal(
    creationSamplerLabel(job),
    '双采样：4 steps · CFG 1.0 · euler / simple · denoise 1.0 → 0.36',
  )
})

test('labels terminal and unknown creation states without inventing cost', () => {
  assert.equal(creationStatusLabel({ status: 'completed' }), '生成完成')
  assert.equal(creationStatusLabel(null, [{ status: 'failed' }]), '执行遇到问题')
  assert.equal(creationStatusLabel(null, [{ tool_name: 'creation_check_workflow', status: 'completed' }]), '未提交生成任务')
  assert.equal(creationCostLabel({ backend: 'remote_api' }), '生成费用未识别')
  assert.deepEqual(creationLoras({ spec: {} }), [])
  assert.equal(creationSamplerLabel({ workflow_id: 'minimax-h3-video' }), '')
})

test('exposes the exact workflow execution snapshot when present', () => {
  const snapshot = { mode: 'exact_workflow', source_file: '无敌图片.json', loras: [] }
  assert.equal(creationWorkflowSnapshot({ spec: { workflow_snapshot: snapshot } }), snapshot)
  assert.equal(creationWorkflowSnapshot({ spec: {} }), null)
})
