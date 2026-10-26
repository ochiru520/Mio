const successful = new Set(['completed', 'succeeded'])
export function activityExpanded(status, hasAnswer, manualChoice = null) {
  if (manualChoice !== null) return manualChoice
  return !(successful.has(status) && hasAnswer)
}

export function settleResponseStream(state, kind, payload = {}) {
  if (kind === 'response_stream_started') return { id: payload.stream_id, text: '', state: 'draft' }
  if (kind === 'response_text_delta' && payload.stream_id === state.id && state.state === 'draft') return { ...state, text: state.text + (payload.text || '') }
  if (kind === 'response_stream_finished' && payload.stream_id === state.id && state.state !== 'committed') return { ...state, state: payload.status === 'generated' ? 'generated' : 'abandoned' }
  if (kind === 'final_response_completed') return { ...state, text: typeof payload.reply === 'string' ? payload.reply : state.text, state: payload.persistence_state && payload.persistence_state !== 'committed' ? 'abandoned' : 'committed' }
  if (['final_response_failed', 'final_response_cancelled'].includes(kind)) return { ...state, state: 'abandoned' }
  return state
}

export function changedFilesFromHistory(changes, taskId) {
  const files = new Map()
  for (const change of [...(changes || [])].reverse()) {
    if (change.task_id !== taskId || !['applied', 'completed', 'verified'].includes(change.status)) continue
    for (const file of change.files || change.file_changes || []) {
      const path = file.path || file.relative_path
      if (!path) continue
      const old = files.get(path)
      files.set(path, { ...file, path, change_id: change.id, change_ids:[...(old?.change_ids || []),change.id], additions: Number(file.additions ?? file.added_lines ?? 0) + (old?.additions || 0), deletions: Number(file.deletions ?? file.removed_lines ?? 0) + (old?.deletions || 0) })
    }
  }
  return [...files.values()]
}
