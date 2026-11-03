const GPT_LINKS = [
  { label: 'GPT-SoVITS 项目与安装说明', url: 'https://github.com/RVC-Boss/GPT-SoVITS' },
  { label: 'Hugging Face 模型下载', url: 'https://huggingface.co/lj1995/GPT-SoVITS' },
  { label: 'ModelScope 模型下载', url: 'https://modelscope.cn/models/AI-ModelScope/GPT-SoVITS' },
]

const GUIDES = {
  mio_v2: {
    title: 'GPT-SoVITS V2（32 token 流式）',
    requirements: '需要兼容的 V2 GPT（.ckpt）与 SoVITS（.pth）权重、参考音频及准确原文。可以使用 Mio 音色，也可以导入自己的 V2 微调音色。',
    compatibility: '此入口支持 V2；V2Pro、V2ProPlus 和 v5 权重需使用各自兼容的运行环境，不能直接混用。模型是否兼容会在加载时检查。',
    setup: '官方模型仓库提供基础模型；你训练的角色音色需要另外导入两份配套权重。下载完成后，还需安装兼容运行环境，再保存并加载所选音色。',
    links: GPT_LINKS,
  },
  genie: {
    title: 'Genie ONNX',
    requirements: '使用转换后的 Genie ONNX 音色包和对应运行引擎。',
    compatibility: '原始 .ckpt / .pth 不能直接作为 Genie ONNX 音色使用。自训 V2 原始权重请选择 V2 流式运行方式。',
    setup: 'Mio 原声音色和 Genie 运行引擎可在「环境与模型」中安装；其他音色按 Genie 项目说明准备。',
    links: [{ label: 'Genie-TTS 项目与模型说明', url: 'https://github.com/High-Logic/Genie-TTS/blob/v2.0.2/README_zh.md' }],
  },
  gpt_sovits: {
    title: '其他原生 GPT-SoVITS 服务',
    requirements: '连接你自行启动的 GPT-SoVITS 原生服务，使用与该服务版本匹配的权重。',
    compatibility: '以服务实际加载版本为准；选择运行方式不会自动下载或切换独立服务里的模型。',
    setup: '按项目说明安装运行环境与基础模型，再配置服务地址和配套音色权重。',
    links: GPT_LINKS,
  },
  gpt_sovits_v5: {
    title: 'GPT-SoVITS v5',
    requirements: '需要 v5 兼容服务与对应版本模型，使用参考音频和准确原文。',
    compatibility: 'V2 微调权重不能直接用于 v5。请按 GPT-SoVITS 官方说明选择对应版本目录与运行环境。',
    setup: '先按项目说明安装所选 v5 模型和服务，再填写 Mio 的 v5 服务地址。',
    links: GPT_LINKS,
  },
  aniflive: {
    title: 'AnifLive-TTS（实验）',
    requirements: '此适配使用配套的 GPT-SoVITS V2 权重、参考音频与准确原文；需要 Windows 64 位和 NVIDIA 显卡。',
    compatibility: 'Mio 的 V2 专用适配支持自训 V2 音色；Genie、V2ProPlus 和 v5 不能混用。仍为实验支持，实际身份以上方运行状态为准。',
    setup: '到“模型与功能”的 Anif 安装面板选择音色。Mio 会准备环境、在本机编译并生成中日试听，满意后再点击使用。可跳过或取消。',
    links: [{ label: 'AnifLive-TTS 项目与模型说明', url: 'https://github.com/Hiruynk/AnifLive-TTS' }],
  },
  indextts2: {
    title: 'IndexTTS',
    requirements: '需要所选版本的 IndexTTS 基础模型、兼容运行环境和一段参考音频。',
    compatibility: 'GPT-SoVITS 微调权重不能用于 IndexTTS；2 与 2.5 的模型和服务需要对应。',
    setup: '从官方项目说明进入对应版本模型下载页；安装后连接 Mio 的 IndexTTS 适配服务。',
    links: [{ label: 'IndexTTS 官方项目与模型下载', url: 'https://github.com/index-tts/index-tts' }],
  },
  cloud: {
    title: '火山引擎云端语音',
    requirements: '无需下载本地语音模型，需要联网并配置语音 API Key。',
    compatibility: '音色与可用模型由服务提供方决定，本地训练权重不会上传或自动转换。',
    setup: '在控制台开通所需语音服务并创建 API Key，再回到 Mio 保存并试听。',
    links: [{ label: '火山引擎语音控制台', url: 'https://console.volcengine.com/speech/new/setting/apikeys' }],
  },
}

export function getVoiceModelGuide(engine, localRuntime, indexVersion = '2.5') {
  const key = engine === 'gpt_sovits' ? (localRuntime || 'genie') : engine
  const guide = GUIDES[key] || GUIDES.genie
  return key === 'indextts2' ? { ...guide, title: `IndexTTS ${indexVersion === '2' ? '2' : '2.5'}` } : guide
}
