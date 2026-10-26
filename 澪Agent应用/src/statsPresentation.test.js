import assert from 'node:assert/strict'
import test from 'node:test'
import { ref } from 'vue'
import { useStatsPresentation } from './composables/useStatsPresentation.js'

test('empty statistics remain readable until a server snapshot arrives', () => {
  const stats = useStatsPresentation(ref({}))
  assert.equal(stats.statsMonthLabel.value, '正在读取')
  assert.deepEqual(stats.statsSummary.value, {})
  assert.deepEqual(stats.statsCalendarCells.value, [])
  assert.deepEqual(stats.statsMoodSeries.value, [])
  assert.equal(stats.statsMoodPath.value, '')
  assert.ok(stats.statsDistribution.value.every(item => item.total === 0 && item.percent === 0 && item.offset === 0))
})

test('calendar uses Monday first, leap days, the logical date and diary availability', () => {
  const snapshot = ref({ year: 2024, month: 2, logical_date: '2024-02-28', calendar: [
    { date: '2024-02-28', title: '今日', daily_thirty_status: 'done' },
    { date: '2024-02-29', title: '未来日记', daily_thirty_status: 'done' },
  ] })
  const stats = useStatsPresentation(snapshot)
  const cells = stats.statsCalendarCells.value
  assert.equal(stats.statsMonthLabel.value, '2024 年 2 月')
  assert.equal(cells.length, 32)
  assert.ok(cells.slice(0, 3).every(cell => cell.blank))
  assert.equal(cells[3].day, 1)
  assert.equal(cells[3].status, 'unknown')
  assert.equal(cells[3].hasDiary, false)
  assert.deepEqual(cells.at(-2), { key: '2024-02-28', blank: false, day: 28, date: '2024-02-28', title: '今日', status: 'done', hasDiary: true, isToday: true })
  assert.equal(cells.at(-1).date, '2024-02-29')
  assert.equal(cells.at(-1).status, 'future')
  assert.equal(cells.at(-1).hasDiary, true)
  snapshot.value = { year: 2024, month: 4, logical_date: '2024-04-01' }
  assert.equal(stats.statsCalendarCells.value[0].day, 1)
  assert.equal(stats.statsCalendarCells.value.length, 30)
})

test('distribution updates cumulative percentages after the source snapshot changes', () => {
  const snapshot = ref({ summary: { by_status: { done: 2, partial: 1, missed: 1 } } })
  const stats = useStatsPresentation(snapshot)
  assert.deepEqual(stats.statsDistribution.value.map(({ key, percent, offset, total }) => ({ key, percent, offset, total })), [
    { key: 'done', percent: 50, offset: 0, total: 4 },
    { key: 'partial', percent: 25, offset: 50, total: 4 },
    { key: 'missed', percent: 25, offset: 75, total: 4 },
    { key: 'unknown', percent: 0, offset: 100, total: 4 },
  ])
  snapshot.value.summary.by_status = { unknown: 3 }
  assert.equal(stats.statsDistribution.value.at(-1).percent, 100)
  assert.equal(stats.statsDistribution.value.at(-1).offset, 0)
})

test('mood trend covers 30 UTC dates, clamps scores and omits missing points from its path', () => {
  const snapshot = ref({ logical_date: '2024-03-01', mood_trend: [
    { date: '2024-02-01', mood_score: 7, mood: '开心' },
    { date: '2024-02-29', mood_score: -2 },
    { date: '2024-03-01', mood_score: 1 },
  ] })
  const stats = useStatsPresentation(snapshot)
  const series = stats.statsMoodSeries.value
  assert.equal(series.length, 30)
  assert.equal(series[0].date, '2024-02-01')
  assert.equal(series[0].score, 5)
  assert.equal(series[0].mood, '开心')
  assert.equal(series.at(-2).date, '2024-02-29')
  assert.equal(series.at(-2).score, 0)
  assert.deepEqual(stats.statsMoodPoints.value.map(item => item.date), ['2024-02-01', '2024-03-01'])
  assert.equal(stats.statsMoodPath.value, 'M42.0 18.0 L680.0 168.0')
  snapshot.value.logical_date = '2024-03-02'
  assert.equal(stats.statsMoodSeries.value.at(-1).date, '2024-03-02')
  assert.equal(stats.statsMoodPoints.value.length, 1)
})
