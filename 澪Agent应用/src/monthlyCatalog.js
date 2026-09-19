/** The server catalog covers all diary months; the dashboard only contains recent days. */
export function monthlyCatalog(reviews, logicalDate) {
  const items = new Map(reviews.map(item => [item.month, { ...item }]))
  const month = String(logicalDate || '').slice(0, 7)
  if (/^\d{4}-\d{2}$/.test(month) && !items.has(month)) {
    items.set(month, { month, markdown_content: '', diary_count: 0, source_diaries: [] })
  }
  return [...items.values()].sort((a, b) => b.month.localeCompare(a.month))
}
