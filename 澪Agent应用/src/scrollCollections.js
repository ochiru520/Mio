export function mergeLoadedItems(previous = [], incoming = [], offset = 0, key = 'id') {
  if (!offset) return incoming
  const updated = new Map(incoming.map(item => [item[key],item]))
  const result=previous.map(item => updated.get(item[key]) || item)
  const known=new Set(previous.map(item => item[key]))
  return [...result,...incoming.filter(item => !known.has(item[key]))]
}
