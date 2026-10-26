// Only unsynchronized drafts remain in this browser window. The memory copy
// survives component changes when sessionStorage is unavailable.
const pendingDrafts = new Map()
const activeOwners = new Map()
const copy = value => JSON.parse(JSON.stringify(value))
export const learningDraftKey = (project, session) => `mio:learning-draft:v1:${encodeURIComponent(project)}:${encodeURIComponent(session)}`

export function mergeLearningDraft(base = {}, local = {}, remote = {}) {
  const answers = { ...(remote.answers || {}) }
  for (const id of new Set([...Object.keys(base.answers || {}), ...Object.keys(local.answers || {})])) {
    if (local.answers?.[id] !== base.answers?.[id]) {
      if (local.answers?.[id] === undefined) delete answers[id]
      else answers[id] = local.answers[id]
    }
  }
  const flags = new Set(remote.flagged_item_ids || [])
  const before = new Set(base.flagged_item_ids || []), after = new Set(local.flagged_item_ids || [])
  for (const id of new Set([...before, ...after])) if (before.has(id) !== after.has(id)) {
    if (after.has(id)) flags.add(id); else flags.delete(id)
  }
  return { answers, flagged_item_ids: [...flags], active_item_id: local.active_item_id !== base.active_item_id ? local.active_item_id : remote.active_item_id || '' }
}

export function createLearningDraftWriter(save, initialRevision = 0, options = {}) {
  let revision = initialRevision, sequence = 0, saved = 0, payload = copy(options.initialPayload || {}), baseline = copy(payload), inflight = null
  let recovered = false, storageError = ''
  const key = options.storageKey
  const owner = `${Date.now()}:${Math.random()}`
  let storage
  try { storage = options.storage ?? globalThis.sessionStorage } catch { storageError = '当前窗口无法使用临时存储；关闭窗口前请先保存。' }
  if (key) {
    let previous = pendingDrafts.get(key)
    try { previous = previous || JSON.parse(storage?.getItem(key) || 'null') } catch { /* Invalid local data is not trusted. */ }
    if (previous?.version === 1 && previous.payload && Number.isInteger(previous.revision)) {
      payload = copy(previous.payload); baseline = copy(previous.baseline || options.initialPayload || {})
      revision = previous.revision; sequence = 1; recovered = true
    }
    activeOwners.set(key, owner)
  }
  function checkpoint() {
    if (!key) return
    if (!activeOwners.has(key)) activeOwners.set(key, owner)
    if (activeOwners.get(key) !== owner) return
    if (saved === sequence) {
      pendingDrafts.delete(key)
      activeOwners.delete(key)
      try { storage?.removeItem(key) } catch { storageError = '已同步草稿，但无法清理窗口临时存储。' }
      return
    }
    const value = { version: 1, owner, revision, payload: copy(payload), baseline: copy(baseline) }
    pendingDrafts.set(key, value)
    try { storage?.setItem(key, JSON.stringify(value)) } catch { storageError = '草稿保留在当前页面进程；临时存储不可用，关闭窗口前请先保存。' }
  }
  function change(value) { payload = copy(value); sequence++; checkpoint(); return sequence }
  async function flush() {
    if (inflight) { await inflight; if (saved !== sequence) return flush(); return }
    if (saved === sequence) return
    const current = sequence, value = copy(payload)
    inflight = Promise.resolve().then(() => save({ ...value, expected_revision: revision })).then(result => {
      if (!Number.isInteger(result?.revision) || result.revision <= revision) throw new Error('草稿保存缺少有效的新版本，未清理本地答案。')
      revision = result.revision; baseline = value; saved = current; checkpoint()
    }).finally(() => { inflight = null })
    await inflight
    if (saved !== sequence) return flush()
  }
  function rebase(remote) {
    if (inflight) throw new Error('请等待当前保存结束后再合并草稿。')
    if (!Number.isInteger(remote?.revision)) throw new Error('最新草稿缺少版本，未覆盖本地答案。')
    const merged = mergeLearningDraft(baseline, payload, remote)
    revision = remote.revision; baseline = copy(remote); change(merged)
    return copy(merged)
  }
  if (recovered) checkpoint()
  return { change, flush, checkpoint, rebase, get dirty() { return saved !== sequence }, get revision() { return revision },
    get payload() { return copy(payload) }, get recovered() { return recovered }, get storageError() { return storageError } }
}
