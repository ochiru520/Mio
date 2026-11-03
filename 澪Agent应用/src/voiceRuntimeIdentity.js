const ENGINE_NAMES = {
  aniflive: 'AnifLive 加速引擎',
  gpt_sovits_v5: 'GPT-SoVITS v5',
  indextts2: 'IndexTTS',
  cloud: '云端语音',
}

function modelName(version) {
  const value = String(version || '').trim()
  if (!value || value === 'unknown') return '服务未报告'
  if (/^v2proplus$/i.test(value)) return 'GPT-SoVITS V2ProPlus'
  if (/^v2$/i.test(value)) return 'GPT-SoVITS V2'
  return value
}

export function voiceRuntimeIdentity(runtime = {}, settings = {}) {
  const running = runtime.service_running === true
  const cloud = runtime.engine === 'cloud'
  const engine = runtime.engine === 'gpt_sovits'
    ? (runtime.local_voice_runtime === 'genie' ? 'Genie ONNX' : 'GPT-SoVITS 原生引擎')
    : (ENGINE_NAMES[runtime.engine] || '尚未确认')
  const profileId = runtime.active_profile_id || runtime.voice_profile || ''
  const activeProfile = runtime.profiles?.[profileId] || settings.voice_profiles?.[profileId]
  const model = modelName(runtime.model_version || runtime.version)
  const originalMio = runtime.original_mio_weights_loaded === true && model === 'GPT-SoVITS V2'
    && ['gpt', 'sovits'].every(kind => runtime.active_weights?.[kind]
      && /^[a-f0-9]{64}$/i.test(runtime.weight_sha256?.[kind] || ''))
  const voice = activeProfile?.name || (originalMio ? '原 Mio 音色' : '')
    || runtime.reference_name || profileId || '服务未报告'
  let source = ''
  if (running && runtime.engine === 'aniflive') {
    if (originalMio) source = '原 Mio V2 微调权重，由 AnifLive 加速。'
    else if (/V2ProPlus/i.test(model)) source = '使用 V2ProPlus 音色包，与原 Mio V2 是不同的模型包。'
    else if (/\bV2\b/i.test(model)) source = '使用 V2 模型包；服务尚未确认是否为原 Mio 权重。'
  } else if (running && runtime.engine === 'gpt_sovits') {
    if (runtime.local_voice_runtime === 'mio_v2') source = '使用所选 V2 微调权重，由原生引擎流式生成。'
    else if (runtime.local_voice_runtime === 'genie') source = '使用转换后的 ONNX 音色包。'
  }
  return {
    engine,
    model: running || (cloud && runtime.cloud_tts?.configured) ? model : '未加载',
    voice: running ? voice : '未加载',
    source,
    running,
    status: cloud ? (runtime.cloud_tts?.configured ? '云端已配置' : '云端未配置')
      : runtime.service_loading ? '正在加载' : running ? '正在运行' : '未运行',
  }
}
