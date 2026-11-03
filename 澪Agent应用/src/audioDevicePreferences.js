export function readAudioInputPreferences(storage = globalThis.localStorage) {
  try {
    return {
      inputDeviceId: storage?.getItem('mio.audio.input') || '',
      mode: storage?.getItem('mio.audio.mode') === 'speakers' ? 'speakers' : 'headphones',
    }
  } catch { return { inputDeviceId: '', mode: 'headphones' } }
}

export function microphoneConstraints(preferences = readAudioInputPreferences()) {
  return {
    ...(preferences.inputDeviceId ? { deviceId: { exact: preferences.inputDeviceId } } : {}),
    echoCancellation: preferences.mode === 'speakers',
    noiseSuppression: true,
    autoGainControl: true,
    channelCount: 1,
    sampleRate: { ideal: 16000 },
  }
}

export function microphoneError(error) {
  if (error.name === 'NotAllowedError') return '麦克风权限被拒绝，请在 Windows 和当前窗口中允许访问后重新开始通话。'
  if (['NotFoundError', 'OverconstrainedError'].includes(error.name)) return '所选麦克风不存在或已断开，请到设置中的设备测试重新选择。'
  if (error.name === 'NotReadableError') return '麦克风暂时无法使用，可能被其他应用占用；请检查后重新开始通话。'
  return error.message || '麦克风启动失败，请检查设备后重试。'
}
