
import * as chatApi from '../services/chatApi.js'
/** Diaries operations. Reactive values and cross-domain callbacks are explicitly injected.
 * This module owns behavior, not application-global mutable state.
 * Dependencies are accessors so async callbacks see the latest state.
 */
import * as diaryApi from '../services/diaryApi.js'

export function useDiaries(deps) {
  async function refreshReviewVersions() {
    const [daily, weekly, monthly] = await Promise.all([
      diaryApi.loadReviews({}, deps.request), diaryApi.loadWeekly({}, deps.request), diaryApi.loadMonthly({}, deps.request),
    ])
    deps.dailyReviews.value = daily; deps.weeklyReviews.value = weekly; deps.monthlyReviews.value = monthly
  }
  async function loadStats(year = 0, month = 0) {
    if (deps.statsLoading.value) return
    deps.statsLoading.value = true
    deps.statsError.value = ''
    try {
      deps.statsData.value = await chatApi.loadStatsByYearMonth(year, month, {}, deps.request)
      deps.statsLoaded.value = true
    } catch (error) {
      deps.statsError.value = error.message
    } finally {
      deps.statsLoading.value = false
    }
  }
  function changeStatsMonth(delta) {
    const current = new Date(Date.UTC(deps.statsData.value.year, deps.statsData.value.month - 1 + delta, 1))
    loadStats(current.getUTCFullYear(), current.getUTCMonth() + 1)
  }
  function openStatsDiary(cell) {
    if (cell.hasDiary) openDiary(cell.date)
  }
  async function loadDiaries() {
    void deps.refreshDayDashboard()
    const requestId = ++deps.diarySearchRequestId
    const query = deps.diarySearch.value.trim()
    try {
      const loadedDiaries = await diaryApi.loadDiariesByQ(query, {}, deps.request)
      if (requestId !== deps.diarySearchRequestId || query !== deps.diarySearch.value.trim()) return
      deps.diaries.value = loadedDiaries
      if (!loadedDiaries.length) {
        deps.selectedDiary.value = null
      } else if (!deps.selectedDiary.value || !loadedDiaries.some((item) => item.date === deps.selectedDiary.value.date)) {
        const firstDiary = await diaryApi.loadDiary(loadedDiaries[0].date, {}, deps.request)
        if (requestId !== deps.diarySearchRequestId || query !== deps.diarySearch.value.trim()) return
        deps.selectedDiary.value = firstDiary
      }
    } catch (error) {
      if (requestId === deps.diarySearchRequestId) deps.errorMessage.value = error.message
    }
  }
  function scheduleDiarySearch() {
    if (deps.diarySearchTimer) window.clearTimeout(deps.diarySearchTimer)
    deps.diarySearchTimer = window.setTimeout(() => {
      deps.diarySearchTimer = null
      void loadDiaries()
    }, 250)
  }
  async function openDiary(date) {
    deps.activeView.value = 'diaries'
    try {
      deps.selectedDiary.value = await diaryApi.loadDiary(date, {}, deps.request)
    } catch (error) {
      deps.errorMessage.value = error.message
    }
  }
  async function generateTodayDiary() {
    deps.diaryBusy.value = 'generate'
    try {
      await diaryApi.generateTodayDiary()
      await loadDiaries()
      await deps.refreshDayDashboard()
      if (deps.logicalDate.value) await openDiary(deps.logicalDate.value)
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.diaryBusy.value = ''
    }
  }
  async function confirmDiary() {
    if (!deps.selectedDiary.value) return
    deps.diaryBusy.value = 'confirm'
    try {
      await diaryApi.submitDiaryConfirm(deps.selectedDiary.value.date, { confirmed: true }, {}, deps.request)
      await openDiary(deps.selectedDiary.value.date)
      await refreshReviewVersions()
      await loadDiaries()
      await deps.refreshDayDashboard()
      if (deps.statsLoaded.value) await loadStats(deps.statsData.value.year, deps.statsData.value.month)
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.diaryBusy.value = ''
    }
  }
  async function updateDiary(date, payload) {
    deps.diaryBusy.value = 'edit'
    try {
      await diaryApi.saveDiary(date, payload, {}, deps.request)
      await loadDiaries()
      await openDiary(date)
      await refreshReviewVersions()
      await deps.refreshDayDashboard()
      if (deps.statsLoaded.value) await loadStats(deps.statsData.value.year, deps.statsData.value.month)
      return true
    } catch (error) {
      deps.errorMessage.value = error.message
      return false
    } finally {
      deps.diaryBusy.value = ''
    }
  }
  async function openDailyReview(date) {
    deps.activeView.value = 'memory'
    deps.memoryTab.value = 'daily'
    deps.selectedDailyDate.value = date
    if (!deps.memoryLoaded.value) await deps.loadMemoryHub()
  }
  async function generateDailyReview(date = deps.selectedDailyDate.value || deps.logicalDate.value) {
    if (!date || deps.reviewBusy.value) return
    deps.selectedDailyDate.value = date
    deps.reviewBusy.value = 'daily'
    deps.errorMessage.value = ''
    try {
      await diaryApi.submitReviewsGenerate(date, {}, deps.request)
      await refreshReviewVersions()
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.reviewBusy.value = ''
    }
  }
  async function generateWeekly(start = deps.selectedWeeklyStart.value || deps.weekStartFor(deps.logicalDate.value)) {
    if (!start || deps.reviewBusy.value) return
    deps.selectedWeeklyStart.value = start
    deps.reviewBusy.value = 'weekly'
    deps.errorMessage.value = ''
    try {
      await diaryApi.submitWeeklyGenerate(start, {}, deps.request)
      await refreshReviewVersions()
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.reviewBusy.value = ''
    }
  }
  async function generateMonthly(month = deps.selectedMonthlyMonth.value || deps.logicalDate.value.slice(0, 7)) {
    if (!month || deps.reviewBusy.value) return
    deps.selectedMonthlyMonth.value = month
    deps.reviewBusy.value = 'monthly'
    deps.errorMessage.value = ''
    try {
      await diaryApi.submitMonthlyGenerate(month, {}, deps.request)
      await refreshReviewVersions()
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.reviewBusy.value = ''
    }
  }
  return { loadStats, changeStatsMonth, openStatsDiary, loadDiaries, scheduleDiarySearch, openDiary, generateTodayDiary, confirmDiary, updateDiary, openDailyReview, generateDailyReview, generateWeekly, generateMonthly }
}
