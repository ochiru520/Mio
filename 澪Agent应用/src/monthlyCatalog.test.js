import test from 'node:test'
import assert from 'node:assert/strict'
import { monthlyCatalog } from './monthlyCatalog.js'

test('month catalog retains a missing review between saved and current months with authoritative counts', () => {
  const months = [
    { month: '2026-07', markdown_content: '七月', diary_count: 25 },
    { month: '2026-08', markdown_content: '', diary_count: 30, source_diaries: [{ date: '2026-08-31' }] },
    { month: '2026-09', markdown_content: '', diary_count: 14 },
  ]
  assert.deepEqual(monthlyCatalog(months, '2026-09-19').map(x => [x.month, x.diary_count]), [
    ['2026-09', 14], ['2026-08', 30], ['2026-07', 25],
  ])
  assert.equal(monthlyCatalog(months, '2026-09-19')[1].source_diaries[0].date, '2026-08-31')
  assert.equal(months[0].month, '2026-07')
})

test('current empty month is selectable without inventing intervening diary months', () => {
  const result = monthlyCatalog([{ month: '2025-12', markdown_content: '旧记录', diary_count: 0 }], '2026-02-01')
  assert.deepEqual(result.map(x => x.month), ['2026-02', '2025-12'])
  assert.equal(result[0].diary_count, 0)
})
