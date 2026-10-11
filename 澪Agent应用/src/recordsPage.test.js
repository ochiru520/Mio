import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const records = fs.readFileSync(new URL('./components/RecordsPage.vue', import.meta.url), 'utf8')
const app = fs.readFileSync(new URL('./App.vue', import.meta.url), 'utf8')
const diaries = fs.readFileSync(new URL('./composables/useDiaries.js', import.meta.url), 'utf8')
const api = fs.readFileSync(new URL('./services/diaryApi.js', import.meta.url), 'utf8')
const css = fs.readFileSync(new URL('./styles/integrated.css', import.meta.url), 'utf8')

test('weekly and monthly secondary rails share the diary collapse control', () => {
  assert.match(records, /recordMode === 'weekly'[\s\S]*period-record-layout'[\s\S]*collapsed: !dateRailExpanded/)
  assert.match(records, /recordMode === 'monthly'[\s\S]*period-record-layout'[\s\S]*collapsed: !dateRailExpanded/)
  assert.match(records, /收起周记栏/)
  assert.match(records, /收起月记栏/)
  // 意图：收起后日期栏变成窄轨，正文区仍占满剩余宽度。
  // 不锁定具体 px——那会随设计调整变化，这里只要求「窄轨 + 自适应主区」这个结构。
  const collapsed = css.match(/\.record-reader-layout\.collapsed,\s*\.period-record-layout\.collapsed\s*\{([^}]*)\}/)
  assert.ok(collapsed, '收起状态应当有独立的栅格定义')
  assert.match(collapsed[1], /grid-template-columns:\s*\d+px\s+minmax\(0,\s*1fr\)/)
})

test('monthly records use a persisted monthly summary endpoint', () => {
  assert.match(api, /listMonthlyReviews\s*=\s*\(\)\s*=>\s*apiRequest\('\/api\/monthly'\)/)
  assert.match(diaries, /request\(`\/api\/monthly\/\$\{month\}\/generate`/)
  assert.match(records, /selectedMonthlyReview\?\.markdown_content/)
  assert.match(records, /renderedMarkdown\(context\.selectedMonthlyReview\.markdown_content\)/)
  assert.match(records, /生成月记/)
})

test('diary search is wired to records context and ignores stale requests', () => {
  assert.match(records, /v-model="context\.diarySearch"[\s\S]*@input="context\.scheduleDiarySearch"/)
  assert.match(app, /provide\('mio-records-page',[\s\S]*diarySearch,[\s\S]*scheduleDiarySearch,/)
  assert.match(diaries, /const requestId = \+\+deps\.diarySearchRequestId/)
  assert.match(diaries, /requestId !== deps\.diarySearchRequestId \|\| query !== deps\.diarySearch\.value\.trim\(\)/)
})
