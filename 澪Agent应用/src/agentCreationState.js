const generationTools = new Set([
  'comfyui_generate_image',
  'comfyui_generate_video',
  'remote_generate_image',
  'creation_get_job',
])

const imageLoraLabels = {
  highres_boost: 'HighresBoost',
  sensual_style: '色色 Anima v5',
  clear_lineart: '清晰线稿',
  soft_cel_pastel: '柔和赛璐璐粉彩',
}

export function findCreationJob(receipts) {
  if (!Array.isArray(receipts)) return null
  for (const receipt of receipts) {
    if (!generationTools.has(String(receipt?.tool_name || ''))) continue
    const job = receipt?.result?.job
    if (job && typeof job === 'object' && job.id) return job
  }
  return null
}

export function creationProgressPercent(job) {
  const value = Number(job?.progress || 0)
  if (!Number.isFinite(value)) return 0
  return Math.round(Math.max(0, Math.min(1, value)) * 100)
}

export function creationStatusLabel(job, receipts = []) {
  const status = String(job?.status || '')
  if (status === 'completed') return '生成完成'
  if (status === 'failed') return '生成失败'
  if (status === 'unknown') return '结果待核对'
  if (status === 'timed_out') return '生成超时'
  if (status === 'cancelled') return '已取消'
  if (status === 'cancel_requested') return '正在取消'
  if (['created', 'submitted', 'queued', 'running'].includes(status)) {
    const progress = creationProgressPercent(job)
    return progress > 0 ? `生成中 ${progress}%` : '已提交，等待生成'
  }
  if (receipts.some((item) => ['failed', 'timed_out'].includes(String(item?.status || '')))) {
    return '执行遇到问题'
  }
  return receipts.length ? '未提交生成任务' : ''
}

export function formatCreationElapsed(seconds) {
  const value = Number(seconds || 0)
  if (!Number.isFinite(value) || value <= 0) return '计时中'
  if (value >= 60) {
    const minutes = Math.floor(value / 60)
    return `${minutes} 分 ${(value - minutes * 60).toFixed(1)} 秒`
  }
  return `${value.toFixed(1)} 秒`
}

export function creationCostLabel(job) {
  if (job?.generation_cost_source === 'local_comfyui' || job?.backend === 'comfyui') {
    return '本地生成 ¥0.000000'
  }
  const cost = job?.generation_cost_yuan
  if (cost === null || cost === undefined || !Number.isFinite(Number(cost))) {
    return '生成费用未识别'
  }
  return `生成费用 ¥${Number(cost).toFixed(6)}`
}

export function creationPrompt(job) {
  return {
    positive: String(job?.spec?.prompt || '').trim(),
    negative: String(job?.spec?.negative_prompt || '').trim(),
  }
}

export function creationLoras(job) {
  const choices = job?.spec?.lora_choices
  if (!Array.isArray(choices)) return []
  return [
    'Turbo v0.1',
    'Turbo v0.2',
    ...choices.map((item) => imageLoraLabels[String(item)]).filter(Boolean),
  ]
}

export function creationWorkflowSnapshot(job) {
  const snapshot = job?.spec?.workflow_snapshot
  return snapshot && typeof snapshot === 'object' ? snapshot : null
}

export function creationSamplerLabel(job) {
  if (job?.workflow_id !== 'anima-2.9b-image') return ''
  return '双采样：4 steps · CFG 1.0 · euler / simple · denoise 1.0 → 0.36'
}
