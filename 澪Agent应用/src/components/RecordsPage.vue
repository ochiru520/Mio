<script setup>
import { computed, inject, reactive, ref, watch } from 'vue'
import {
  Activity,
  Archive,
  BookOpen,
  CalendarDays,
  ChartNoAxesCombined,
  Check,
  ChevronLeft,
  ChevronRight,
  Download,
  FilePenLine,
  History,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Save,
  Trash2,
  X,
} from '@lucide/vue'

const context = inject('mio-records-page')
if (!context) throw new Error('记录页上下文未初始化')
const recordMode = ref(context.activeView === 'stats' ? 'growth' : 'daily')
const dateRailExpanded = ref(true)
const memoryView = ref('remembered')
const diaryEditing = ref(false)
const diaryDraft = ref({ markdown_content: '', daily_thirty_status: 'unknown', mood_tags: '' })
const followUpResultDrafts = reactive({})

watch(() => context.activeView, (view) => {
  if (view === 'stats') recordMode.value = 'growth'
  else if (view === 'diaries' && recordMode.value === 'growth') recordMode.value = 'daily'
})

watch(() => context.selectedDiary?.date, () => {
  diaryEditing.value = false
})

function chooseRecordMode(mode) {
  recordMode.value = mode
  context.activeView = mode === 'growth' ? 'stats' : 'diaries'
  if (mode === 'growth' && !context.statsLoaded) context.loadStats()
}

function excerpt(markdown = '') {
  const sectionTitles = new Set([
    '今日事件', '今日情绪', '今日成长', '每日三十', '做得不错',
    '可以调整的地方', '明天最小行动', '今日主线', '状态判断',
    '逃避与耗电', 'AI观察',
  ])
  for (const rawLine of String(markdown).split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || /^```/.test(line) || /^#{1,6}\s+/.test(line) || /^[-|:\s]+$/.test(line)) continue
    const cleaned = line
      .replace(/^\s*(?:[-*+]\s+|\d+[.)]\s+|>\s*)/, '')
      .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
      .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
      .replace(/[>*_`~]/g, '')
      .replace(/\s+/g, ' ')
      .trim()
    const label = cleaned.replace(/[：:]$/, '')
    if (!cleaned || sectionTitles.has(label) || /^\d{4}[-年/.]\d{1,2}/.test(cleaned)) continue
    return cleaned.slice(0, 70)
  }
  return ''
}

function isGenericDiaryTitle(diary) {
  const title = String(diary?.title || '').trim()
  const date = String(diary?.date || '').trim()
  return !title || title === date || title === `${date} 的日记`
}

function diaryListTitle(diary) {
  return isGenericDiaryTitle(diary)
    ? (excerpt(diary?.markdown_content) || '当天记录')
    : diary.title
}

function diaryListDetail(diary) {
  const state = `${context.statusLabel(diary.daily_thirty_status)} · ${diary.confirmed_at ? '已确认' : '未确认'}`
  if (isGenericDiaryTitle(diary)) return state
  const summary = excerpt(diary.markdown_content)
  return summary ? `${summary} · ${state}` : state
}

const activeMonthDiaries = computed(() => (context.diaries || [])
  .filter((diary) => String(diary.date || '').startsWith(`${context.selectedMonthlyMonth}-`))
  .sort((a, b) => b.date.localeCompare(a.date)))

const memoryCategories = [
  ['identity', '关于我'], ['preference', '偏好与习惯'], ['relationship', '重要的人与关系'],
  ['experience', '经历'], ['project', '项目'], ['other', '其他'],
]
const groupedMemories = computed(() => memoryCategories.map(([id, label]) => ({
  id,
  label,
  items: (context.memoryData.structured || []).filter((item) => item.category === id || (id === 'identity' && item.category === 'person')),
})).filter((group) => group.items.length))
const pendingCandidates = computed(() => context.memoryData.structured_candidates || [])
const currentThreads = computed(() => (context.memoryData.threads || []).filter((item) => !item.follow_up_after))
const followUpThreads = computed(() => (context.memoryData.threads || []).filter((item) => item.follow_up_after))
const restorableMemoryHistory = computed(() => (context.memoryData.structured_history || [])
  .filter((item) => ['superseded', 'archived'].includes(item.status)))
const recentFollowUpResults = computed(() => context.memoryData.follow_up_results || [])
const diaryLifeSteps = computed(() => context.selectedDiary?.life_loop?.steps || [])

function beginFollowUpResult(thread) {
  followUpResultDrafts[thread.id] = {
    outcome: 'completed',
    summary: '',
    adjustment: '',
    next_follow_up_after: '',
  }
}

function cancelFollowUpResult(threadId) {
  delete followUpResultDrafts[threadId]
}

async function submitFollowUpResult(thread) {
  const draft = followUpResultDrafts[thread.id]
  if (!draft?.summary?.trim()) return
  const saved = await context.recordFollowUpResult(thread, {
    ...draft,
    summary: draft.summary.trim(),
    adjustment: draft.adjustment.trim(),
  })
  if (saved) delete followUpResultDrafts[thread.id]
}

function addNaturalMemory() {
  context.newStructuredMemory.layer = 'L0'
  context.newStructuredMemory.memory_key = ''
  context.addStructuredMemory()
}

function startDiaryEdit() {
  if (!context.selectedDiary) return
  diaryDraft.value = {
    markdown_content: context.selectedDiary.markdown_content || '',
    daily_thirty_status: context.selectedDiary.daily_thirty_status || 'unknown',
    mood_tags: context.selectedDiary.mood_tags || '',
  }
  diaryEditing.value = true
}

async function saveDiaryEdit() {
  if (!context.selectedDiary || !diaryDraft.value.markdown_content.trim()) return
  const saved = await context.updateDiary(context.selectedDiary.date, diaryDraft.value)
  if (saved) diaryEditing.value = false
}
</script>

<template>
  <section v-if="context.activeView !== 'memory'" class="records-hub">
    <nav class="record-mode-tabs" aria-label="日记类型">
      <button type="button" :class="{ active: recordMode === 'daily' }" @click="chooseRecordMode('daily')"><BookOpen :size="16" />日记</button>
      <button type="button" :class="{ active: recordMode === 'weekly' }" @click="chooseRecordMode('weekly')"><CalendarDays :size="16" />周记</button>
      <button type="button" :class="{ active: recordMode === 'monthly' }" @click="chooseRecordMode('monthly')"><History :size="16" />月记</button>
      <button type="button" :class="{ active: recordMode === 'growth' }" @click="chooseRecordMode('growth')"><ChartNoAxesCombined :size="16" />成长报告</button>
    </nav>

    <div v-if="recordMode === 'daily'" :class="['record-reader-layout', { collapsed: !dateRailExpanded }]">
      <aside class="record-date-rail">
        <header><span>全部日记</span><button type="button" :title="dateRailExpanded ? '收起日期栏' : '展开日期栏'" @click="dateRailExpanded = !dateRailExpanded"><component :is="dateRailExpanded ? PanelLeftClose : PanelLeftOpen" :size="15" /></button></header>
        <label v-if="dateRailExpanded" class="record-search"><Search :size="14" /><input v-model="context.diarySearch" type="search" placeholder="搜索日记" @input="context.loadDiaries" /></label>
        <div class="record-date-list">
          <button v-for="diary in context.diaries" :key="diary.date" type="button" :class="{ active: context.selectedDiary?.date === diary.date }" @click="context.openDiary(diary.date)">
            <time>{{ dateRailExpanded ? diary.date : diary.date.slice(5) }}</time>
            <span v-if="dateRailExpanded"><strong>{{ diaryListTitle(diary) }}</strong><small>{{ diaryListDetail(diary) }}</small></span>
          </button>
        </div>
      </aside>
      <article v-if="context.selectedDiary" class="diary-reader integrated-diary-reader">
        <header><div><span>{{ context.selectedDiary.date }}</span><h2>{{ context.selectedDiary.title }}</h2></div><div class="reader-actions"><a class="icon-button" :href="`/diaries/${context.selectedDiary.date}/download`" title="下载 Markdown"><Download :size="17" /></a><button v-if="!diaryEditing" class="icon-button" type="button" title="编辑日记" @click="startDiaryEdit"><FilePenLine :size="17" /></button><button v-if="diaryEditing" class="icon-button" type="button" title="取消编辑" @click="diaryEditing = false"><X :size="17" /></button><button v-if="diaryEditing" class="primary-button" type="button" :disabled="Boolean(context.diaryBusy) || !diaryDraft.markdown_content.trim()" @click="saveDiaryEdit"><Save :size="16" />保存</button><button v-else class="primary-button" type="button" :disabled="Boolean(context.diaryBusy) || Boolean(context.selectedDiary.confirmed_at)" @click="context.confirmDiary"><Check :size="16" />{{ context.selectedDiary.confirmed_at ? '已确认' : '确认日记' }}</button></div></header>
        <ol v-if="diaryLifeSteps.length" class="diary-life-loop" aria-label="生活闭环状态">
          <li v-for="step in diaryLifeSteps" :key="step.id" :class="`status-${step.status}`" :title="step.detail">
            <i><Check v-if="step.status === 'complete'" :size="13" /><span v-else /></i>
            <strong>{{ step.label }}</strong>
            <small>{{ step.detail }}</small>
          </li>
        </ol>
        <form v-if="diaryEditing" class="inline-diary-editor" @submit.prevent="saveDiaryEdit">
          <div><label>今日成长状态<select v-model="diaryDraft.daily_thirty_status"><option value="unknown">未确认</option><option value="done">完成</option><option value="partial">部分完成</option><option value="missed">未完成</option></select></label><label>标签<input v-model="diaryDraft.mood_tags" placeholder="用顿号分隔" /></label></div>
          <textarea v-model="diaryDraft.markdown_content" aria-label="日记 Markdown 正文" spellcheck="false" />
        </form>
        <div v-else class="markdown-body" v-html="context.renderedMarkdown(context.selectedDiary.markdown_content)" />
      </article>
      <div v-else class="reader-empty compact-reader-empty"><BookOpen :size="30" /><strong>还没有可查看的日记</strong><span>日记会由自动流程或对话指令生成</span></div>
    </div>

    <div v-else-if="recordMode === 'weekly'" :class="['period-record-layout', { collapsed: !dateRailExpanded }]">
      <aside class="period-record-rail">
        <header><strong v-if="dateRailExpanded">周记</strong><span v-if="dateRailExpanded">{{ context.weeklyReviewItems.length }} 周</span><button type="button" :title="dateRailExpanded ? '收起周记栏' : '展开周记栏'" @click="dateRailExpanded = !dateRailExpanded"><component :is="dateRailExpanded ? PanelLeftClose : PanelLeftOpen" :size="15" /></button></header>
        <button v-for="item in context.weeklyReviewItems" :key="item.week_start" type="button" :class="{ active: context.selectedWeeklyStart === item.week_start }" @click="context.selectedWeeklyStart = item.week_start"><time>{{ dateRailExpanded ? `${item.week_start} 至 ${item.week_end}` : item.week_start.slice(5) }}</time><span v-if="dateRailExpanded">{{ item.markdown_content ? excerpt(item.markdown_content) : '这一周还没有形成周记' }}</span></button>
      </aside>
      <article v-if="context.selectedWeeklyReview?.markdown_content" class="period-reader"><header><div><span>{{ context.selectedWeeklyReview.week_start }} 至 {{ context.selectedWeeklyReview.week_end }}</span><h2>这一周的记录</h2></div><button class="secondary-button" type="button" :disabled="Boolean(context.reviewBusy)" @click="context.generateWeekly()"><RefreshCw :class="{ spin: context.reviewBusy === 'weekly' }" :size="15" />重新生成</button></header><div class="markdown-body" v-html="context.renderedMarkdown(context.selectedWeeklyReview.markdown_content)" /></article>
      <div v-else class="reader-empty compact-reader-empty"><CalendarDays :size="30" /><strong>这一周还没有周记</strong><button class="primary-button" type="button" :disabled="Boolean(context.reviewBusy)" @click="context.generateWeekly()"><RefreshCw :class="{ spin: context.reviewBusy === 'weekly' }" :size="15" />生成周记</button></div>
    </div>

    <div v-else-if="recordMode === 'monthly'" :class="['period-record-layout', { collapsed: !dateRailExpanded }]">
      <aside class="period-record-rail">
        <header><strong v-if="dateRailExpanded">月记</strong><span v-if="dateRailExpanded">{{ context.monthlyReviewItems.length }} 月</span><button type="button" :title="dateRailExpanded ? '收起月记栏' : '展开月记栏'" @click="dateRailExpanded = !dateRailExpanded"><component :is="dateRailExpanded ? PanelLeftClose : PanelLeftOpen" :size="15" /></button></header>
        <button v-for="item in context.monthlyReviewItems" :key="item.month" type="button" :class="{ active: context.selectedMonthlyMonth === item.month }" @click="context.selectedMonthlyMonth = item.month"><time>{{ dateRailExpanded ? item.month : item.month.slice(5) }}</time><span v-if="dateRailExpanded">{{ item.markdown_content ? excerpt(item.markdown_content) : `${item.diary_count || 0} 篇日记，尚未总结` }}</span></button>
      </aside>
      <article v-if="context.selectedMonthlyReview?.markdown_content" class="period-reader monthly-reader"><header><div><span>{{ context.selectedMonthlyReview.month_start }} 至 {{ context.selectedMonthlyReview.month_end }}</span><h2>{{ context.selectedMonthlyReview.month }} 月记</h2></div><button class="secondary-button" type="button" :disabled="Boolean(context.reviewBusy)" @click="context.generateMonthly()"><RefreshCw :class="{ spin: context.reviewBusy === 'monthly' }" :size="15" />重新生成</button></header><div class="markdown-body" v-html="context.renderedMarkdown(context.selectedMonthlyReview.markdown_content)" /><details v-if="activeMonthDiaries.length" class="monthly-source-diaries"><summary>查看本月 {{ activeMonthDiaries.length }} 篇日记</summary><div class="monthly-diary-list"><button v-for="diary in activeMonthDiaries" :key="diary.date" type="button" @click="chooseRecordMode('daily'); context.openDiary(diary.date)"><time>{{ diary.date }}</time><strong>{{ diary.title || `${diary.date} 的日记` }}</strong><span>{{ context.statusLabel(diary.daily_thirty_status) }}</span></button></div></details></article>
      <div v-else class="reader-empty compact-reader-empty"><History :size="30" /><strong>这个月还没有形成总结</strong><span>{{ activeMonthDiaries.length ? `将根据 ${activeMonthDiaries.length} 篇日记总结整个月` : '这个月还没有日记' }}</span><button v-if="activeMonthDiaries.length" class="primary-button" type="button" :disabled="Boolean(context.reviewBusy)" @click="context.generateMonthly()"><RefreshCw :class="{ spin: context.reviewBusy === 'monthly' }" :size="15" />生成月记</button></div>
    </div>

    <section v-else class="growth-report-page">
      <header><div><ChartNoAxesCombined :size="21" /><span><h2>成长报告</h2><p>从日记、今日成长和情绪里看看最近的变化</p></span></div><button type="button" title="刷新" @click="context.loadStats(context.statsData.year, context.statsData.month)"><RefreshCw :class="{ spin: context.statsLoading }" :size="16" /></button></header>
      <div class="growth-metrics"><article><span>日记</span><strong>{{ context.statsSummary.total || 0 }}<small>篇</small></strong></article><article><span>成长完成率</span><strong>{{ ((context.statsSummary.completion_rate || 0) * 100).toFixed(1) }}<small>%</small></strong></article><article><span>当前连续</span><strong>{{ context.statsSummary.current_streak || 0 }}<small>天</small></strong></article><article><span>最长连续</span><strong>{{ context.statsSummary.longest_streak || 0 }}<small>天</small></strong></article></div>
      <div class="growth-dashboard"><section class="stats-calendar-panel"><header><div><CalendarDays :size="18" /><h3>{{ context.statsMonthLabel }}</h3></div><div><button type="button" @click="context.changeStatsMonth(-1)"><ChevronLeft :size="16" /></button><button type="button" @click="context.changeStatsMonth(1)"><ChevronRight :size="16" /></button></div></header><div class="stats-weekdays"><span v-for="day in ['一','二','三','四','五','六','日']" :key="day">{{ day }}</span></div><div class="stats-calendar-grid"><span v-for="cell in context.statsCalendarCells" :key="cell.key" :class="{ blank: cell.blank }"><button v-if="!cell.blank" type="button" :class="[`status-${cell.status}`, { today: cell.isToday }]" :disabled="!cell.hasDiary" @click="context.openStatsDiary(cell)">{{ cell.day }}</button></span></div></section><section class="growth-summary-panel"><h3>近 30 天情绪</h3><div v-if="context.statsMoodPoints.length" class="growth-mood-bars"><i v-for="point in context.statsMoodPoints" :key="point.date" :style="{ height: `${20 + point.score * 14}%` }" :title="`${point.date} · ${point.score}/5`" /></div><p v-else>还没有足够的情绪记录</p></section></div>
    </section>
  </section>

  <section v-else class="memory-center">
    <header class="memory-center-heading"><div><Archive :size="21" /><span><h2>{{ context.mioDisplayName }}记住的你</h2><p>这里保存{{ context.mioDisplayName }}真正会在对话里使用的长期记忆</p></span></div><button type="button" title="刷新" @click="context.loadMemoryHub()"><RefreshCw :class="{ spin: context.memoryLoading }" :size="16" /></button></header>
    <nav class="memory-view-tabs"><button type="button" :class="{ active: memoryView === 'remembered' }" @click="memoryView = 'remembered'">记住的事</button><button type="button" :class="{ active: memoryView === 'current' }" @click="memoryView = 'current'">正在进行</button><button type="button" :class="{ active: memoryView === 'followup' }" @click="memoryView = 'followup'">约定与跟进</button></nav>
    <div v-if="context.memoryLoading && !context.memoryLoaded" class="center-state"><RefreshCw class="spin" :size="22" /><span>正在读取记忆</span></div>
    <template v-else-if="memoryView === 'remembered'">
      <section class="memory-understanding"><span>{{ context.mioDisplayName }}现在怎样认识你</span><p v-if="context.memoryData.structured?.length">{{ context.memoryData.structured.slice(0, 5).map((item) => item.content).join('；') }}</p><p v-else>还没有形成稳定的长期认识</p></section>
      <form class="memory-natural-add" @submit.prevent="addNaturalMemory"><select v-model="context.newStructuredMemory.category"><option v-for="item in memoryCategories" :key="item[0]" :value="item[0]">{{ item[1] }}</option></select><input v-model="context.newStructuredMemory.content" :placeholder="`用一句自然的话告诉${context.mioDisplayName}要记住什么`" /><button type="submit" :disabled="Boolean(context.memoryBusy)"><Plus :size="15" />记住</button></form>
      <section v-if="pendingCandidates.length" class="memory-candidate-panel"><header><strong>等待你确认</strong><span>{{ pendingCandidates.length }} 条</span></header><article v-for="memory in pendingCandidates" :key="memory.id"><p>{{ memory.content }}</p><div><button type="button" @click="context.rejectMemoryCandidate(memory)">忽略</button><button type="button" class="primary-button" @click="context.confirmMemoryCandidate(memory)">确认记住</button></div></article></section>
      <div v-if="groupedMemories.length" class="memory-category-grid"><section v-for="group in groupedMemories" :key="group.id"><header><strong>{{ group.label }}</strong><span>{{ group.items.length }}</span></header><article v-for="memory in group.items" :key="memory.id"><p>{{ memory.content }}</p><div class="memory-item-actions"><details><summary>查看依据</summary><span>来源：{{ memory.source_conversation_id || '手动记录' }}<template v-if="memory.source_message_id"> · 消息 #{{ memory.source_message_id }}</template><br />置信度：{{ Math.round(Number(memory.confidence || 0) * 100) }}% · {{ context.memoryLayerLabel(memory.layer) }}</span></details><button type="button" @click="context.editStructuredMemory(memory)">编辑</button><button type="button" title="忘记" @click="context.archiveStructuredMemory(memory)"><Trash2 :size="14" /></button></div></article></section></div>
      <div v-else class="reader-empty compact-reader-empty"><Archive :size="30" /><strong>{{ context.mioDisplayName }}还没有形成长期记忆</strong></div>
      <details v-if="restorableMemoryHistory.length" class="memory-version-history">
        <summary><History :size="15" />历史版本 <span>{{ restorableMemoryHistory.length }}</span></summary>
        <div>
          <article v-for="memory in restorableMemoryHistory" :key="memory.id">
            <span><b>{{ context.memoryCategoryLabel(memory.category) }}</b><small>{{ memory.status === 'superseded' ? '被新版本替代' : '已停用' }} · {{ context.formatShortTime(memory.updated_at) }}</small></span>
            <p>{{ memory.content }}</p>
            <button type="button" :disabled="Boolean(context.memoryBusy)" @click="context.restoreStructuredMemory(memory)"><RotateCcw :size="14" />恢复这个版本</button>
          </article>
        </div>
      </details>
    </template>
    <section v-else-if="memoryView === 'current'" class="memory-thread-list"><article v-for="thread in currentThreads" :key="thread.id"><textarea v-model="thread.content" rows="2" /><footer><span>{{ context.formatShortTime(thread.created_at) }}</span><div><button type="button" @click="context.deleteMemoryThread(thread.id)">删除</button><button type="button" @click="context.saveMemoryThread(thread)">保存</button><button type="button" @click="context.resolveThread(thread.id)">完成</button></div></footer></article><div v-if="!currentThreads.length" class="reader-empty compact-reader-empty"><Activity :size="30" /><strong>当前没有正在进行的事情</strong></div></section>
    <section v-else class="memory-follow-up-view">
      <div class="memory-thread-list">
        <article v-for="thread in followUpThreads" :key="thread.id">
          <textarea v-model="thread.content" rows="2" />
          <footer><span>跟进时间：{{ context.formatShortTime(thread.follow_up_after) }}</span><div><button type="button" @click="context.deleteMemoryThread(thread.id)">删除</button><button type="button" @click="context.saveMemoryThread(thread)">保存</button><button type="button" class="primary" @click="beginFollowUpResult(thread)">记录结果</button></div></footer>
          <form v-if="followUpResultDrafts[thread.id]" class="follow-up-result-form" @submit.prevent="submitFollowUpResult(thread)">
            <div class="follow-up-outcome-control" aria-label="完成情况">
              <button v-for="item in [['completed','完成'],['partial','部分完成'],['not_completed','未完成']]" :key="item[0]" type="button" :class="{ active: followUpResultDrafts[thread.id].outcome === item[0] }" @click="followUpResultDrafts[thread.id].outcome = item[0]">{{ item[1] }}</button>
            </div>
            <label>真实结果<textarea v-model="followUpResultDrafts[thread.id].summary" rows="2" placeholder="发生了什么，以你的反馈为准" /></label>
            <label>后续调整<input v-model="followUpResultDrafts[thread.id].adjustment" placeholder="例如缩小目标、改到晚上或保持原计划" /></label>
            <label v-if="followUpResultDrafts[thread.id].outcome !== 'completed'">下次跟进<input v-model="followUpResultDrafts[thread.id].next_follow_up_after" type="datetime-local" /></label>
            <footer><button type="button" @click="cancelFollowUpResult(thread.id)">取消</button><button class="primary" type="submit" :disabled="Boolean(context.memoryBusy) || !followUpResultDrafts[thread.id].summary.trim()"><Save :size="14" />保存结果</button></footer>
          </form>
        </article>
        <div v-if="!followUpThreads.length" class="reader-empty compact-reader-empty"><Check :size="30" /><strong>没有等待跟进的约定</strong></div>
      </div>
      <section v-if="recentFollowUpResults.length" class="follow-up-result-history">
        <header><strong>最近结果</strong><span>{{ recentFollowUpResults.length }} 条</span></header>
        <article v-for="result in recentFollowUpResults" :key="result.id">
          <div><strong>{{ result.thread_content || `跟进 #${result.thread_id}` }}</strong><span :class="`outcome-${result.outcome}`">{{ result.outcome_label }}</span></div>
          <p>{{ result.summary || '没有补充说明' }}</p>
          <small v-if="result.adjustment">调整：{{ result.adjustment }}</small>
          <small v-if="result.next_follow_up_after">下次：{{ context.formatShortTime(result.next_follow_up_after) }}</small>
        </article>
      </section>
    </section>
  </section>
</template>
