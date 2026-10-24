export function sourceIsConfigured(state) {
  const config = state?.configuration
  return Number(config?.revision) > 0 && Boolean(config.backend_root?.trim() && config.desktop_root?.trim())
}

export function installedBuildLabel(build) {
  if (typeof build === 'string') return build
  if (!build || typeof build !== 'object') return '尚未读取'
  return build.version || build.app_version || build.build_id || (build.source_mode ? '源码运行' : '未登记构建版本')
}
