import { computed } from 'vue'

/** Read-only record statistics derived from the current server snapshot. */
export function useStatsPresentation(statsData) {
  const statsSummary = computed(() => statsData.value.summary || {})
  const statsMonthLabel = computed(() => statsData.value.year && statsData.value.month
    ? `${statsData.value.year} 年 ${statsData.value.month} 月`
    : '正在读取')
  const statsCalendarCells = computed(() => {
    const year = Number(statsData.value.year || 0)
    const month = Number(statsData.value.month || 0)
    if (!year || !month) return []
    const entryMap = new Map((statsData.value.calendar || []).map((item) => [item.date, item]))
    const firstDay = new Date(Date.UTC(year, month - 1, 1))
    const offset = (firstDay.getUTCDay() + 6) % 7
    const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate()
    const cells = Array.from({ length: offset }, (_, index) => ({ key: `blank-${index}`, blank: true }))
    for (let day = 1; day <= daysInMonth; day += 1) {
      const date = `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
      const entry = entryMap.get(date)
      cells.push({
        key: date,
        blank: false,
        day,
        date,
        title: entry?.title || '',
        status: date > statsData.value.logical_date ? 'future' : (entry?.daily_thirty_status || 'unknown'),
        hasDiary: Boolean(entry),
        isToday: date === statsData.value.logical_date,
      })
    }
    return cells
  })
  const statsDistribution = computed(() => {
    const byStatus = statsSummary.value.by_status || {}
    const config = [
      { key: 'done', label: '完成', color: '#388166' },
      { key: 'partial', label: '部分完成', color: '#b17a32' },
      { key: 'missed', label: '未完成', color: '#b24b4b' },
      { key: 'unknown', label: '未记录', color: '#94a0a9' },
    ]
    const total = config.reduce((sum, item) => sum + Number(byStatus[item.key] || 0), 0)
    let offset = 0
    return config.map((item) => {
      const value = Number(byStatus[item.key] || 0)
      const percent = total ? (value / total) * 100 : 0
      const segment = { ...item, value, percent, offset, total }
      offset += percent
      return segment
    })
  })
  const statsMoodSeries = computed(() => {
    const logical = statsData.value.logical_date
    if (!logical) return []
    const byDate = new Map((statsData.value.mood_trend || []).map((item) => [item.date, item]))
    const current = new Date(`${logical}T00:00:00Z`)
    return Array.from({ length: 30 }, (_, index) => {
      const target = new Date(current)
      target.setUTCDate(current.getUTCDate() - (29 - index))
      const date = target.toISOString().slice(0, 10)
      const item = byDate.get(date) || {}
      const score = Math.max(0, Math.min(5, Number(item.mood_score || 0)))
      return {
        date,
        mood: item.mood || '',
        score,
        x: 42 + (index / 29) * 638,
        y: score ? 18 + ((5 - score) / 4) * 150 : 0,
      }
    })
  })
  const statsMoodPoints = computed(() => statsMoodSeries.value.filter((item) => item.score))
  const statsMoodPath = computed(() => statsMoodPoints.value
    .map((item, index) => `${index ? 'L' : 'M'}${item.x.toFixed(1)} ${item.y.toFixed(1)}`)
    .join(' '))

  return { statsSummary, statsMonthLabel, statsCalendarCells, statsDistribution, statsMoodSeries, statsMoodPoints, statsMoodPath }
}
