export function normalizeAgentView(view) {
  if (['creation', 'creation-image', 'creation-video'].includes(view)) return 'chat'
  return ['home', 'chat', 'tasks', 'settings'].includes(view) ? view : 'home'
}

export function filterConversationsForWorkspace(conversations, workspace) {
  const items = Array.isArray(conversations) ? conversations : []
  return items.filter((item) => (
    workspace === 'agent' ? item?.kind === 'agent' : item?.kind !== 'agent'
  ))
}
