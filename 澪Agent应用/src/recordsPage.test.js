import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const records = fs.readFileSync(new URL('./components/RecordsPage.vue', import.meta.url), 'utf8')
const app = fs.readFileSync(new URL('./App.vue', import.meta.url), 'utf8')
const api = fs.readFileSync(new URL('./services/diaryApi.js', import.meta.url), 'utf8')
const css = fs.readFileSync(new URL('./styles/integrated.css', import.meta.url), 'utf8')

test('weekly and monthly secondary rails share the diary collapse control', () => {
  assert.match(records, /recordMode === 'weekly'[\s\S]*period-record-layout'[\s\S]*collapsed: !dateRailExpanded/)
  assert.match(records, /recordMode === 'monthly'[\s\S]*period-record-layout'[\s\S]*collapsed: !dateRailExpanded/)
  assert.match(records, /收起周记栏/)
  assert.match(records, /收起月记栏/)
  assert.match(css, /\.period-record-layout\.collapsed\s*\{[^}]*grid-template-columns:\s*92px/)
})

test('monthly records use a persisted monthly summary endpoint', () => {
  assert.match(api, /listMonthlyReviews\s*=\s*\(\)\s*=>\s*apiRequest\('\/api\/monthly'\)/)
  assert.match(app, /request\(`\/api\/monthly\/\$\{month\}\/generate`/)
  assert.match(records, /selectedMonthlyReview\?\.markdown_content/)
  assert.match(records, /renderedMarkdown\(context\.selectedMonthlyReview\.markdown_content\)/)
  assert.match(records, /生成月记/)
})
