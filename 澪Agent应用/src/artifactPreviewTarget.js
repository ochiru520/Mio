export function artifactPreviewTarget(attachment, artifacts = []) {
  if (!attachment) return null
  let id = attachment.artifact_id
  for (const value of [attachment.artifact_url, attachment.url]) {
    const match = typeof value === 'string' && value.match(/^\/api\/artifacts\/([a-zA-Z0-9_-]+)\/(?:download|content)(?:\?.*)?$/)
    if (!id && match) id = match[1]
  }
  const known = artifacts.find(item => (id && item.id === id) || (attachment.url && item.url === attachment.url))
  if (known) return known
  if (!id || !/^[a-zA-Z0-9_-]+$/.test(id)) return null
  return { ...attachment, id, url: `/api/artifacts/${id}/download` }
}
