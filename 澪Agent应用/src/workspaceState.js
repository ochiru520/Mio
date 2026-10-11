export function normalizeAgentView(view) {
  if (['creation', 'creation-image', 'creation-video'].includes(view)) return 'chat'
  return ['home', 'chat', 'projects', 'tasks', 'settings'].includes(view) ? view : 'home'
}

export function filterConversationsForWorkspace(conversations, workspace) {
  const items = Array.isArray(conversations) ? conversations : []
  return items.filter((item) => (
    workspace === 'agent' ? item?.kind === 'agent' : item?.kind !== 'agent'
  ))
}

export function findAgentModeConversation(conversations, index, mode, projectId = '') {
  return (Array.isArray(conversations) ? conversations : []).find(item => (
    item?.id && (index?.modes?.[item.id] || 'work') === mode
      && (index?.projects?.[item.id] || '') === projectId
  ))
}
