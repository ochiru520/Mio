const VALID_VIEW_IDS = new Set([
  'onboarding',
  'home',
  'chat',
  'diaries',
  'memory',
  'tasks',
  'companion',
  'settings',
  'stats',
])

const VALID_SETTINGS_SECTION_IDS = new Set([
  'general',
  'appearance',
  'profile',
  'conversation',
  'diary',
  'models',
  'qq',
  'pet',
  'data',
  'advanced',
])

export const ACTIVE_VIEW_HEARTBEAT_MS = 5000

export function buildActiveViewReport(viewId, sectionId = '', visible = true) {
  const view = String(viewId || '').trim().toLowerCase()
  if (!VALID_VIEW_IDS.has(view)) throw new Error(`不支持的主应用页面：${view || '空'}`)
  const section = view === 'settings' ? String(sectionId || '').trim().toLowerCase() : ''
  if (section && !VALID_SETTINGS_SECTION_IDS.has(section)) {
    throw new Error(`不支持的设置分区：${section}`)
  }
  return {
    view_id: view,
    section_id: section,
    visible: Boolean(visible),
  }
}
